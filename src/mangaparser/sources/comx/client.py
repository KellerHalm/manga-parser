"""Com-X (com-x.life) — источник по открытым sitemap-файлам.

Сайт на DLE закрыт антибот-челленджем: корень, категории и карточки отдают
404 с редиректом на `/_c?t=...`, где браузер обязан пройти proof-of-work,
снять отпечаток окружения (WebGL/canvas/таймзона) и пройти детект
автоматизации (`navigator.webdriver`, CDP). Такой обход парсер писать нельзя,
поэтому источник берёт ровно то, что сайт публикует открытым:

    /sitemap.xml          — индекс вложенных sitemap
    /news_pages*.xml      — адреса карточек: <loc> + <lastmod>, ~28 300 штук

Отсюда ограничения, которые важно понимать до использования:

    * доступны только `id`, `url`, название из slug и `lastmod` — ни жанров,
      ни рейтинга, ни описаний, ни глав в sitemap нет и на сайте они лежат
      за челленджем, поэтому `chapters()` возвращает пусто;
    * `search()` работает по уже скачанным sitemap, а не по серверу: серверный
      `/search/` запрещён в `robots.txt` и закрыт тем же челленджем;
    * `details()` не ходит на сайт, а достаёт запись из кэша sitemap.

    python main.py comx dump --csv           # ~28 300 адресов, 3 запроса
    python main.py comx search "schenok"
    python main.py comx details 34100
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from ...core.http import Http, SourceError, build_session
from ...core.models import Item
from ...core.ports import Checkpoint
from . import parsing

SITE = "https://com-x.life"
SITEMAP_URL = f"{SITE}/sitemap.xml"

PER_PAGE = 200  # sitemap отдаётся целиком, страница — это срез в памяти
DEFAULT_DELAY = 0.5

HEADERS = {
    "Accept": "application/xml,text/xml,application/json;q=0.9,*/*;q=0.8",
    "Referer": f"{SITE}/",
}

#: Порядок обхода; в самих sitemap записи уже идут от новых к старым.
SORTS = {
    "": "Сначала новые",
    "oldest": "Сначала старые",
}


class ComXError(SourceError):
    """Ошибка Com-X: сеть или неожиданный формат sitemap."""


@dataclass
class Title(Item):
    """Карточка Com-X в том виде, в каком её публикует sitemap."""

    id: int
    title: str
    url: str
    slug: str
    updated_at: str | None = None
    #: Какой sitemap-файл дал запись — полезно при разборе выгрузки.
    sitemap: str | None = None

    source = "comx"

    @property
    def uid(self) -> str:
        return str(self.id)

    @property
    def key(self) -> str:
        """Ключ для details() — числовой id тайтла."""
        return str(self.id)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Title:
        return cls(
            id=int(row.get("id") or 0),
            title=row.get("title") or "",
            url=row.get("url") or "",
            slug=row.get("slug") or "",
            updated_at=row.get("updated_at"),
            sitemap=row.get("sitemap"),
        )


class ComXClient:
    """Клиент каталога Com-X по sitemap-файлам."""

    name = "comx"
    #: Модель записи источника — нужна для обогащения выгрузки.
    item_class = Title
    dedupe_key = "id"
    key_hint = "id"
    #: Обогащать нечего: карточка и каталог отдают одни и те же поля sitemap.
    enrichable = False

    def __init__(self, session: Any | None = None, *, delay: float = DEFAULT_DELAY) -> None:
        self.http = Http(session or build_session(headers=HEADERS), error=ComXError)
        self.delay = delay
        self._rows: list[dict[str, Any]] | None = None

    # ---------- низкий уровень ----------

    def _get(self, url: str) -> str:
        return self.http.get_text(url)

    def sitemap_files(self) -> list[str]:
        """Вложенные sitemap-файлы из индекса."""
        files = parsing.sitemaps(self._get(SITEMAP_URL))
        if not files:
            raise ComXError("Не удалось прочитать индекс sitemap")
        return files

    def rows(self) -> list[dict[str, Any]]:
        """
        Все записи каталога, собранные из всех sitemap.

        Результат кэшируется на клиенте: sitemap-ов всего три, а `catalog()`
        и `search()` берут данные именно отсюда.
        """
        if self._rows is None:
            self._rows = [
                row
                for url in self.sitemap_files()
                for row in parsing.entries(self._get(url), url.rsplit("/", 1)[-1])
            ]
        return self._rows

    # ---------- публичный API ----------

    def catalog(self, page: int = 1, count: int = PER_PAGE, sort: str = "") -> list[Title]:
        """Страница каталога — срез уже скачанного sitemap, без новых запросов."""
        return [Title.from_row(r) for r in self._slice(page, count, sort)]

    def search(self, query: str, page: int = 1, count: int = PER_PAGE) -> list[Title]:
        """
        Поиск по скачанному sitemap, а не по серверу.

        Серверный `/search/` закрыт челленджем и запрещён в `robots.txt`,
        поэтому ищем локально: в название тайтла попадает транслитерация,
        поэтому сравнение идёт и по slug, и по title.
        """
        needle = query.casefold()
        hits = [
            row
            for row in self.rows()
            if needle in row["slug"].casefold() or needle in row["title"].casefold()
        ]
        start = (max(page, 1) - 1) * count
        return [Title.from_row(r) for r in hits[start: start + count]]

    def details(self, key: str) -> Title:
        """
        Карточка тайтла.

        Страницу сайта получить нельзя (челлендж), поэтому отдаём запись
        из sitemap: это всё, что сайт публикует.
        """
        needle = str(key).casefold().removesuffix(".html").split("/")[-1]
        for row in self.rows():
            if str(row["id"]) == needle or row["slug"].casefold() == needle:
                return Title.from_row(row)
        raise ComXError(f"Тайтл не найден в sitemap: {key}")

    def chapters(self, key: str) -> list[dict[str, Any]]:
        """Главы недоступны: список глав отдаётся только за челленджем."""
        return []

    def chapter_pages(self, key: str, chapter: Any, **_: Any) -> list[str]:
        """
        Страницы глав недоступны — и обойти это нельзя.

        Список глав и адреса картинок лежат на страницах сайта, а они закрыты
        антибот-челленджем; ни sitemap, ни что-либо ещё их не содержит. Метод
        поднимает ошибку сразу, чтобы пустой список не выглядел как «главы
        без картинок».
        """
        raise ComXError(
            "Com-X закрыт антибот-челленджем: список глав и страницы глав недоступны "
            "без браузера с JavaScript"
        )

    def stats(self) -> int:
        """Всего тайтлов в sitemap."""
        return len(self.rows())

    def total_pages(self, count: int = PER_PAGE) -> int:
        return (self.stats() + count - 1) // count

    # ---------- обход каталога ----------

    def iter_catalog(
        self,
        *,
        delay: float | None = None,
        max_pages: int | None = None,
        count: int = PER_PAGE,
        sort: str = "",
        checkpoint: Checkpoint | None = None,
        on_page: Callable[..., None] | None = None,
        **_: Any,
    ) -> Iterator[Title]:
        """
        Обход каталога по страницам уже скачанного sitemap.

        Сетевых запросов три (индекс + файлы), поэтому пауза между страницами
        не нужна: `delay` ждёт только когда sitemap ещё не скачан. Прогресс
        всё равно пишется в чекпоинт — из него потом собираются JSON/CSV
        и офлайн-индекс.
        """
        downloaded = self._rows is not None
        pause = delay if delay is not None else self.delay
        page = 1
        seen: set[int] = set()
        while max_pages is None or page <= max_pages:
            # паузу держим только пока sitemap реально качается с сервера
            if not downloaded:
                time.sleep(pause)
                downloaded = True

            items = self.catalog(page=page, count=count, sort=sort)
            if not items:
                if on_page:
                    on_page("empty", page)
                return

            fresh = [t for t in items if t.id not in seen]
            seen.update(t.id for t in fresh)
            if not fresh:
                if on_page:
                    on_page("duplicates", page)
                return

            if checkpoint is not None:
                checkpoint.append([t.flat() for t in fresh])
                checkpoint.flush()
            if on_page:
                on_page("page", page, len(fresh), len(seen))

            yield from fresh
            page += 1

    # ---------- служебное ----------

    def _slice(self, page: int, count: int, sort: str) -> list[dict[str, Any]]:
        rows: Sequence[dict[str, Any]] = self.rows()
        if sort == "oldest":
            rows = sorted(rows, key=lambda r: r["id"])
        start = (max(page, 1) - 1) * count
        return list(rows[start: start + count])


def build() -> ComXClient:
    return ComXClient()


def describe() -> tuple[str, str]:
    return "Com-X", "com-x.life — открытые sitemap (id, url, название, lastmod)"


__all__ = ["Title", "ComXClient", "ComXError", "SORTS", "PER_PAGE", "DEFAULT_DELAY"]