"""Состояние приложения: один экземпляр на сессию Flet.

Создаётся при старте и кладётся в `page.services`/`page.data`, чтобы экраны
брали его оттуда, а не плодили копии БД и кэша.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import flet as ft

from .hub import SourceHub, site_of
from .images import ImageStore
from .store import Library

#: Источники в порядке меню «Обзор»: у первых полная выдача глав и страниц.
SOURCE_ORDER = ("remanga", "mangalib", "buzumanga", "inkstory", "selfmanga", "desu", "comx")

#: Подписи источников для интерфейса.
SOURCE_LABELS = {
    "remanga": "ReManga",
    "mangalib": "MangaLIB",
    "buzumanga": "BuZuManga",
    "inkstory": "InkStory",
    "selfmanga": "SelfManga",
    "desu": "Desu.Me",
    "comx": "Com-X",
}

#: Что источник реально умеет — подсказка в интерфейсе вместо пустых экранов.
SOURCE_NOTES = {
    "comx": "Только каталог: сайт закрыт антиботом",
    "desu": "Доступна первая глава тома",
    "selfmanga": "Главы 18+ открыты без картинок",
    "inkstory": "Список глав обрезан на первой странице",
    "mangalib": "Карточка без описания и жанров",
}


def label_of(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


@dataclass
class Downloads:
    """Фоновые закачки глав: главы одного тайтла качаются по очереди."""

    page: ft.Page
    store: ImageStore
    library: Library
    referer: Callable[[str], str | None] | None = None
    active: dict[str, bool] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def running(self, source: str, key: str, chapter_key: str) -> bool:
        return self.active.get(f"{source}|{key}|{chapter_key}", False)

    def start(
        self,
        source: str,
        key: str,
        title: str,
        chapter: object,
        label: str | None,
        client: object,
        pages: list[str] | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Скачать одну главу в фоне."""
        self._run(source, key, title, [(chapter, label, pages)], client, on_done)

    def queue(
        self,
        source: str,
        key: str,
        title: str,
        entries: list[tuple[object, str | None, list[str] | None]],
        client: object,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        """Скачать список глав одним воркером, друг за другом.

        На главу можно завести и по потоку, но тогда главы одного
        источника ломают его троттлинг (сайт отбивает 429), а сессия
        `requests` не потокобезопасна. Поэтому очередь общая.
        """
        self._run(source, key, title, entries, client, on_done)

    def _run(
        self,
        source: str,
        key: str,
        title: str,
        entries: list[tuple[object, str | None, list[str] | None]],
        client: object,
        on_done: Callable[[], None] | None,
    ) -> None:
        from .store import chapter_key as ck

        pending = [(chapter, label, pages) for chapter, label, pages in entries if not self.active.get(f"{source}|{key}|{ck(chapter)}")]
        if not pending:
            if on_done:
                on_done()
            return
        for chapter, _label, _pages in pending:
            self.active[f"{source}|{key}|{ck(chapter)}"] = True

        def worker() -> None:
            for chapter, label, pages in pending:
                uid = f"{source}|{key}|{ck(chapter)}"
                try:
                    urls = pages if pages is not None else list(client.chapter_pages(key, chapter))
                    if not urls:
                        raise RuntimeError("У главы нет доступных страниц")
                    referer = self.referer(source) if self.referer else None
                    folder = self.store.chapter_folder(source, key, chapter)
                    self.store.download_chapter(urls, folder, referer=referer)
                    self.library.add_download(source, key, chapter, title, label, len(urls))
                    self.errors.pop(uid, None)
                except Exception as exc:
                    self.errors[uid] = str(exc)
                finally:
                    self.active[uid] = False
                    try:
                        if on_done:
                            # Сначала перерисовать экран (on_done мутирует
                            # контролы), потом отправить дельту клиенту.
                            on_done()
                    except Exception:
                        pass
                    self.page.update()

        # Через `run_thread`, а не свой `threading.Thread`: из чужого потока
        # обновления до клиента не доходят, и скачивание выглядит как
        # нажатие, которое ничего не делает.
        self.page.run_thread(worker)


class AppState:
    """Всё, что живёт дольше одного экрана."""

    def __init__(self, page: ft.Page, db_path: Path | None = None) -> None:
        self.page = page
        self.library = Library(db_path)
        self.images = ImageStore()
        self.hub = SourceHub()
        self.downloads = Downloads(page, self.images, self.library, self.referer)
        self.reader_mode = self.library.get_setting("reader_mode", "webtoon")
        self.accent = self.library.get_setting("accent", "#FF4D8D")
        self._referers: dict[str, str | None] = {}

    def referer(self, source: str) -> str | None:
        """Адрес сайта источника для заголовка `Referer` при загрузке картинок."""
        if source not in self._referers:
            self._referers[source] = site_of(self.hub, source)
        return self._referers[source]

    def cover(self, url: str | None, source: str) -> str | None:
        """Локальный путь обложки: из кэша, иначе скачать с Referer источника."""
        if not url:
            return None
        cached = self.images.cached_cover(url)
        return cached or self.images.fetch_cover(url, self.referer(source))

    def page_image(self, url: str, source: str) -> str | None:
        """Локальный путь страницы главы: из кэша, иначе скачать с Referer."""
        cached = self.images.cached_page(url)
        return cached or self.images.fetch_page(url, self.referer(source))

    def ordered_sources(self) -> list[str]:
        available = self.hub.names()
        known = [s for s in SOURCE_ORDER if s in available]
        return known + [s for s in available if s not in known]

    def reader_mode_label(self) -> str:
        return "Вебтунс" if self.reader_mode == "webtoon" else "Страницы"

    def toggle_reader_mode(self) -> str:
        self.reader_mode = "paged" if self.reader_mode == "webtoon" else "webtoon"
        self.library.set_setting("reader_mode", self.reader_mode)
        return self.reader_mode

    def close(self) -> None:
        self.images.close()
        self.library.close()