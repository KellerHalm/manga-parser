"""BuzuManga (buzumanga.com) — источник на собственном JSON API сайта.

Сайт — SPA на React, в HTML нет данных, но бэкенд отдаёт чистый JSON:

    /api/manga?limit=&offset=&sort=       — каталог (сортировка по id/updated/year)
    /api/manga/search?q=&limit=&offset=   — поиск (сервер отдаёт максимум 10 записей)
    /api/manga/{id}                       — карточка (описание, закладки)
    /api/manga/{id}/chapters              — главы тайтла (за один ответ)
    /api/manga/{id}/chapters/{number}/pages — страницы главы (пути картинок)
    /api/manga/stats                      — всего тайтлов в базе

Особенности, которые отличают источник от MangaLIB/ReManga:

    * пагинация каталога — через `offset`, а не `page`, сервер отдаёт до 120
      записей за раз; 100 — безопасный размер страницы;
    * `id` тайтла — это и есть ключ (`/api/manga/2634`); slug в API отсутствует,
      он собирается из названия только для красивого URL — см. `slug.slugify`;
    * адреса картинок приходят относительными путями (`/objects/...`) и
      раздаются с отдельного CDN, поэтому в выгрузку пишется путь как есть,
      а абсолютный URL собирается функцией `image_url`;
    * главы одного номера бывают у разных переводчиков, а страницы главы
      адресуются именно номером — при дублях отдаётся одна из версий.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ...core.http import Http, SourceError, Throttle, build_session
from ...core.models import Item
from ...core.models import chapter_field as _get
from ...core.ports import Checkpoint
from ...core.text import to_float, to_int
from . import slug

SITE = "https://buzumanga.com"
API = f"{SITE}/api"
CATALOG_URL = f"{API}/manga"
SEARCH_URL = f"{API}/manga/search"
STATS_URL = f"{API}/manga/stats"

#: Хост раздачи картинок (его публикует SPA-оболочка в __IMAGE_CDN_ORIGIN__).
IMAGE_ORIGIN = "https://img.buzumanga.org"

PER_PAGE = 100  # сервер принимает до 120, 100 — с запасом
SEARCH_PER_PAGE = 10  # жёсткий потолок серверного поиска
DEFAULT_DELAY = 0.5

HEADERS = {
    "Accept": "application/json",
    "Referer": f"{SITE}/",
    # Сервер рвёт keep-alive сразу после ответа: следующий запрос на том же
    # сокете падает с ConnectionError (urllib3 его переоткрывает, но это
    # двойной RTT и лишние 429). Поэтому соединение на запрос, а не пул.
    "Connection": "close",
}

#: Сортировки, которые сервер реально различает (остальные игнорируются).
SORT_FIELDS = {
    "": "Сначала новые (по id)",
    "updated": "По дате обновления",
    "year": "По году публикации",
}

#: Технические статусы сайта -> человеческие (как в других источниках проекта).
STATUS_LABELS = {
    "ongoing": "Выпускается",
    "finished": "Завершён",
    "frozen": "Заморожен",
    "licensed": "Лицензировано",
}

#: Статусы перевода; неизвестное значение остаётся как есть (см. `from_api`).
TRANSLATION_LABELS = {
    "translating": "Переводится",
    "completed": "Перевод завершён",
    "discontinued": "Перевод прекращён",
}

_ID_RE = re.compile(r"\d+")


class BuZuMangaError(SourceError):
    """Ошибка BuZuManga: сеть, 404/422 или неожиданный формат ответа."""


def image_url(path: str | None, origin: str = IMAGE_ORIGIN) -> str | None:
    """Относительный путь картинки -> абсолютный URL на CDN."""
    if not path:
        return None
    return path if path.startswith("http") else f"{origin}{path}"


def manga_id(key: str) -> str:
    """
    Приводит любой ключ к числовому id.

    Принимает и `2634`, и хвост URL `2634-nenasytnyi-berserk`, и полный URL:
    на сайте карточка адресуется как `/manga/<id>-<slug>`, а API — по `<id>`.
    """
    match = _ID_RE.search(str(key))
    if not match:
        raise BuZuMangaError(f"Не удалось извлечь id тайтла из ключа: {key!r}")
    return match.group(0)


@dataclass
class Manga(Item):
    """Тайтл BuZuManga в нормализованном виде."""

    id: int
    title: str
    url: str
    alt_title: str | None = None
    cover: str | None = None
    description: str | None = None
    genres: list[str] = field(default_factory=list)
    chapters: int | None = None
    rating: float | None = None
    votes: int | None = None
    year: int | None = None
    status: str | None = None
    #: Дата оригинального релиза (есть только в карточке, не в списке).
    original_release: str | None = None
    translation_status: str | None = None
    content_type: str | None = None
    work_type: str | None = None
    age_rating: str | None = None
    bookmarks: int | None = None
    is_hentai: bool | None = None
    is_blocked: bool | None = None
    created_at: str | None = None
    updated_at: str | None = None

    source = "buzumanga"

    @property
    def uid(self) -> str:
        return str(self.id)

    @property
    def key(self) -> str:
        """Ключ для details()/chapters() — числовой id тайтла."""
        return str(self.id)

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Manga:
        ident = int(raw.get("id") or 0)
        title = raw.get("title") or ""
        status = raw.get("status")
        bookmarks = (raw.get("bookmarkStats") or {}).get("total")
        return cls(
            id=ident,
            title=title,
            url=manga_url(ident, title),
            alt_title=raw.get("alternativeTitles") or None,
            cover=raw.get("coverImagePath") or None,
            description=raw.get("description") or None,
            genres=[g for g in (raw.get("tags") or []) if g],
            chapters=to_int(raw.get("totalChapters")),
            rating=to_float(raw.get("averageRating")),
            votes=to_int(raw.get("ratingCount")),
            year=to_int(raw.get("publicationYear")),
            status=STATUS_LABELS.get(status, status),
            original_release=raw.get("originalReleaseDate") or None,
            translation_status=TRANSLATION_LABELS.get(raw.get("translationStatus"), raw.get("translationStatus")),
            content_type=raw.get("contentType") or None,
            work_type=raw.get("workType") or None,
            age_rating=raw.get("ageRating") or None,
            bookmarks=to_int(bookmarks),
            is_hentai=raw.get("isHentai"),
            is_blocked=raw.get("isBlocked"),
            created_at=raw.get("createdAt"),
            updated_at=raw.get("updatedAt"),
        )


def manga_url(ident: int, title: str) -> str:
    """Адрес карточки на сайте: /manga/<id>-<slug>."""
    tail = slug.slugify(title)
    return f"{SITE}/manga/{ident}-{tail}" if tail else f"{SITE}/manga/{ident}"


class BuZuMangaClient:
    """Клиент каталога BuZuManga."""

    name = "buzumanga"
    #: Модель записи источника — нужна для обогащения выгрузки.
    item_class = Manga
    #: Поле дедупликации при финализации выгрузки.
    dedupe_key = "id"
    #: Что нужно этому источнику для адресации карточки/глав.
    key_hint = "id"

    def __init__(self, session: Any | None = None, *, delay: float = DEFAULT_DELAY) -> None:
        self.http = Http(session or build_session(headers=HEADERS), error=BuZuMangaError)
        self.delay = delay

    # ---------- низкий уровень ----------

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.http.get_json(url, params)

    # ---------- публичный API ----------

    def catalog(
        self,
        page: int = 1,
        count: int = PER_PAGE,
        sort: str = "",
        content_type: str = "",
        tags: str | list[str] = "",
        **_: Any,
    ) -> list[Manga]:
        """
        Страница каталога.

        Пагинация серверная через `offset`, поэтому страница N — это
        смещение (N-1) * count. `tags` принимает строку или список жанров
        (как на сайте — несколько жанров через запятую).
        """
        params = _clean({
            "limit": min(count, PER_PAGE),
            "offset": (max(page, 1) - 1) * min(count, PER_PAGE),
            "sort": sort,
            "contentType": content_type,
            "tags": ",".join(tags) if isinstance(tags, (list, tuple)) else tags,
        })
        data = self._get(CATALOG_URL, params)
        return [Manga.from_api(r) for r in data] if isinstance(data, list) else []

    def search(self, query: str, page: int = 1, **_: Any) -> list[Manga]:
        """
        Серверный поиск.

        Ответ приходит обёрнутым в `{"items": [...]}` и содержит всего шесть
        полей (id, title, обложка, альт. названия, типы) — без рейтинга,
        статусов и жанров, их добирает `enrich`.
        """
        params = {"q": query, "limit": SEARCH_PER_PAGE, "offset": (max(page, 1) - 1) * SEARCH_PER_PAGE}
        data = self._get(SEARCH_URL, params)
        items = data.get("items") if isinstance(data, dict) else None
        return [Manga.from_api(r) for r in items or []]

    def details(self, key: str) -> Manga:
        """Карточка тайтла: к списочным полям добавляется описание."""
        raw = self._get(f"{CATALOG_URL}/{manga_id(key)}")
        if not isinstance(raw, dict) or not raw.get("id"):
            raise BuZuMangaError(f"Не удалось разобрать карточку: {key}")
        return Manga.from_api(raw)

    def chapters(self, key: str) -> list[dict[str, Any]]:
        """Главы тайтла — сервер отдаёт их все одним ответом."""
        ident = manga_id(key)
        payload = self._get(f"{CATALOG_URL}/{ident}/chapters", {"limit": 2000})
        rows = payload.get("chapters") if isinstance(payload, dict) else None
        return [self._chapter(r, ident) for r in rows or []]

    def chapter_pages(self, key: str, chapter: Any, **_: Any) -> list[str]:
        """
        Пути картинок главы (для загрузки изображений).

        Страницы адресуются номером главы, а не её id: у одного номера бывает
        несколько переводов, и эндпоинт отдаёт одну из версий. Номер берётся
        из записи главы, поэтому её можно передать из `chapters()` как есть.

        API отдаёт пути относительные — абсолютные адреса лежат на отдельном
        CDN, и без префикса `image_url` скачать картинку нельзя.
        """
        number = _get(chapter, "number")
        if number is None:
            raise BuZuMangaError(f"У главы нет номера — страницы недоступны: {chapter!r}")
        ident = manga_id(key)
        data = self._get(f"{CATALOG_URL}/{ident}/chapters/{number}/pages")
        pages = data.get("pages") if isinstance(data, dict) else None
        return [image_url(page) for page in pages or [] if page]

    def stats(self) -> int:
        """Всего тайтлов в базе — нужно, чтобы знать размер обхода."""
        data = self._get(STATS_URL)
        return to_int(data.get("totalCount")) or 0 if isinstance(data, dict) else 0

    def total_pages(self, count: int = PER_PAGE) -> int:
        """Число страниц каталога по данным /api/manga/stats."""
        size = min(count, PER_PAGE)
        return (self.stats() + size - 1) // size

    @staticmethod
    def _chapter(raw: dict[str, Any], ident: str) -> dict[str, Any]:
        number = raw.get("number")
        uploader = raw.get("uploadedBy") or {}
        guild = raw.get("guild") or {}
        return {
            "id": raw.get("id"),
            "number": number,
            "title": raw.get("title") or None,
            "url": f"{SITE}/manga/{ident}/read/{number}",
            "page_count": raw.get("pageCount"),
            "translator": uploader.get("nickname"),
            "guild": guild.get("name"),
            "total_likes": raw.get("totalLikes"),
            "accessible": raw.get("accessible"),
            "release_date": raw.get("releaseDate") or raw.get("createdAt"),
        }

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
    ) -> Iterator[Manga]:
        """
        Постраничный обход каталога с JSONL-чекпоинтом.

        Пустая страница или страница без новых id означает конец каталога.
        При 429 пауза удваивается (до 60 с) и страница повторяется.
        """
        throttle = Throttle(delay if delay is not None else self.delay)
        size = min(count, PER_PAGE)
        page = 1
        seen: set[int] = set()
        while max_pages is None or page <= max_pages:
            try:
                items = self.catalog(page=page, count=size, sort=sort)
            except BuZuMangaError:
                throttle.on_rate_limit()
                if on_page:
                    on_page("rate-limited", page)
                continue

            throttle.on_success()
            if not items:
                if on_page:
                    on_page("empty", page)
                return

            fresh = [m for m in items if m.id not in seen]
            seen.update(m.id for m in fresh)
            if not fresh:
                if on_page:
                    on_page("duplicates", page)
                return

            if checkpoint is not None:
                checkpoint.append([m.flat() for m in fresh])
                checkpoint.flush()
            if on_page:
                on_page("page", page, len(fresh), len(seen))

            yield from fresh
            page += 1
            throttle.wait()


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Пустые параметры не отправляются: сервер трактует их как значения по умолчанию."""
    return {k: v for k, v in params.items() if v not in (None, "", []) and v != 0}


def build() -> BuZuMangaClient:
    return BuZuMangaClient()


def describe() -> tuple[str, str]:
    return "BuZuManga", "buzumanga.com — JSON API /api/manga, каталог, поиск, главы"


__all__ = [
    "Manga", "BuZuMangaClient", "BuZuMangaError", "manga_id", "image_url",
    "SORT_FIELDS", "STATUS_LABELS", "TRANSLATION_LABELS", "PER_PAGE", "DEFAULT_DELAY",
]