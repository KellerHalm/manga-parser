"""InkStory (inkstory.net) — источник без публичного API, данные из SSR-разметки.

Каталог рендерится на сервере, поэтому используются два источника данных:

    1. JSON-LD schema.org (ItemList -> ComicSeries) — названия, URL, обложки,
       жанры, описание; это основной и самый надёжный путь;
    2. видимый текст карточки — дополнительные поля (автор, год, главы, статусы).

Списки глав и страниц глав приходят в том же SSR-ответе, поэтому отдельного
API источник не использует вовсе.

Пагинация: /content?page=N (с 1), 40 книг на страницу.
Поиск:     /content?search=<query> — тот же эндпоинт, что в SearchAction разметки.
Сортировка: ?sort=createdAt | viewsCount | ...
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ...core.http import Http, SourceError, Throttle, build_session
from ...core.models import Item, chapter_field
from ...core.ports import Checkpoint
from ...core.text import strip_tags, to_float, to_int
from . import parsing

SITE = "https://inkstory.net"
CATALOG_URL = f"{SITE}/content"
BOOK_URL = f"{SITE}/content/{{slug}}"
CHAPTERS_URL = f"{SITE}/content/{{slug}}/chapters"
CHAPTER_URL = f"{SITE}/content/{{slug}}/{{id}}"
PER_PAGE = 40
TOTAL_PAGES = 537  # из пагинации каталога
DEFAULT_DELAY = 0.7

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": f"{SITE}/",
}


class InkStoryError(SourceError):
    """Ошибка InkStory: сеть, 404 или неожиданный формат разметки."""


@dataclass
class Book(Item):
    """Книга InkStory в нормализованном виде."""

    slug: str
    url: str
    title: str
    cover: str | None = None
    description: str | None = None
    genres: list[str] = field(default_factory=list)
    rating: float | None = None
    votes: int | None = None
    chapters: int | None = None
    author: str | None = None
    artist: str | None = None
    status: str | None = None
    year: int | None = None
    views: int | None = None
    likes: int | None = None
    #: Страна оригинала (в JSON-LD такого поля нет).
    source_name: str | None = None
    #: Издатели («Komik Şeyler, Planet Manga»).
    publishers: list[str] = field(default_factory=list)
    #: Переводчики, ведущие тайтл на сайте.
    translators: list[str] = field(default_factory=list)
    #: Журнал, в котором выходит оригинал.
    journals: list[str] = field(default_factory=list)
    #: Другие названия тайтла (оригинальное, японское, альтернативное).
    alt_titles: list[str] = field(default_factory=list)

    source = "inkstory"

    @property
    def uid(self) -> str:
        return self.slug

    @property
    def key(self) -> str:
        """Ключ для details()/chapters() — slug."""
        return self.slug or self.url.rstrip("/").rsplit("/", 1)[-1]

    @classmethod
    def from_jsonld(cls, item: dict[str, Any], slug: str, url: str = "") -> Book:
        """
        Карточка из JSON-LD ComicSeries.

        Рейтинг лежит в `aggregateRating` (ratingValue + ratingCount) — на
        странице книги он есть, в каталоге нет, поэтому заполняется здесь.
        """
        aggregate = item.get("aggregateRating") or {}
        return cls(
            slug=slug,
            url=url or item.get("url") or BOOK_URL.format(slug=slug),
            title=item.get("name") or "",
            cover=item.get("image"),
            description=strip_tags(item.get("description") or "") or None,
            genres=[g for g in (item.get("genre") or []) if g],
            rating=to_float(aggregate.get("ratingValue")),
            votes=to_int(aggregate.get("ratingCount")),
        )


class InkStoryClient:
    """Клиент каталога InkStory."""

    name = "inkstory"
    #: Модель записи источника — нужна для обогащения выгрузки.
    item_class = Book
    dedupe_key = "slug"
    key_hint = "slug"

    def __init__(self, session: Any | None = None, *, delay: float = DEFAULT_DELAY) -> None:
        self.http = Http(session or build_session(headers=HEADERS), error=InkStoryError)
        self.delay = delay

    # ---------- низкий уровень ----------

    def _html(self, url: str, params: dict[str, Any] | None = None) -> str:
        return self.http.get_text(url, params)

    # ---------- публичный API ----------

    def catalog(self, page: int = 1, search: str = "", sort: str = "") -> list[Book]:
        """Страница каталога. search/sort используют те же параметры, что и сайт."""
        params: dict[str, Any] = {}
        if page and page > 1:
            params["page"] = page
        if search:
            params["search"] = search
        if sort:
            params["sort"] = sort

        page_html = self._html(CATALOG_URL, params)
        books: dict[str, Book] = {}
        for item in parsing.collection_items(page_html):
            url = item.get("url") or ""
            slug = url.rstrip("/").rsplit("/", 1)[-1]
            if not slug:
                continue
            books[slug] = Book.from_jsonld(item, slug, url)
        return list(books.values())

    def search(self, query: str, page: int = 1) -> list[Book]:
        return self.catalog(page=page, search=query)

    def details(self, slug: str) -> Book:
        page = self._html(BOOK_URL.format(slug=slug))
        node = parsing.series_node(page)
        if not node:
            raise InkStoryError(f"Не удалось разобрать страницу: {slug}")
        book = Book.from_jsonld(node, slug)
        return parsing.apply_details_fields(book, page)

    def chapters(self, slug: str) -> list[dict[str, Any]]:
        """
        Список глав — вкладка «Главы» карточки.

        Отдаёт только первичную виртуальную страницу списка: остальное сайт
        догружает через закрытый в `robots.txt` `/api`, поэтому `?page=N`
        одну и ту же страницу и не помогает.
        """
        return parsing.chapters_from_page(self._html(CHAPTERS_URL.format(slug=slug)), SITE)

    def chapter_pages(self, key: str, chapter: Any, **_: Any) -> list[str]:
        """
        Адреса картинок главы.

        Адрес главы берётся из записи (`url` из `chapters()`), иначе
        собирается из slug и id главы — на сайте глава адресуется uuid.
        """
        url = chapter_field(chapter, "url")
        if not url:
            slug, ident = str(key).strip("/"), chapter_field(chapter, "id")
            if not slug or not ident:
                raise InkStoryError(f"Не удалось определить адрес главы: {chapter!r}")
            url = CHAPTER_URL.format(slug=slug, id=ident)
        return parsing.reader_pages(self._html(url))

    def total_pages(self) -> int:
        """Число страниц каталога из пагинации (последняя может обновиться)."""
        return parsing.total_pages(self._html(CATALOG_URL), TOTAL_PAGES)

    # ---------- обход каталога ----------

    def iter_catalog(
        self,
        *,
        delay: float | None = None,
        max_pages: int | None = None,
        sort: str = "",
        checkpoint: Checkpoint | None = None,
        on_page: Callable[..., None] | None = None,
        **_: Any,
    ) -> Iterator[Book]:
        """
        Обход каталога с JSONL-чекпоинтом: переживает краш и 429.

        Страница без новых книг означает конец каталога (сайт отдаёт те же данные).
        """
        throttle = Throttle(delay if delay is not None else self.delay)
        page = 1
        seen: set[str] = set()
        while max_pages is None or page <= max_pages:
            try:
                books = self.catalog(page=page, sort=sort)
            except InkStoryError:
                throttle.on_rate_limit()
                if on_page:
                    on_page("rate-limited", page)
                continue

            throttle.on_success()
            fresh = [b for b in books if b.slug not in seen]
            seen.update(b.slug for b in fresh)
            if not fresh:
                if on_page:
                    on_page("end", page)
                return

            if checkpoint is not None:
                checkpoint.append([b.flat() for b in fresh])
                checkpoint.flush()
            if on_page:
                on_page("page", page, len(fresh), len(seen))

            yield from fresh
            page += 1
            throttle.wait()


def build() -> InkStoryClient:
    return InkStoryClient()


def describe() -> tuple[str, str]:
    return "InkStory", "inkstory.net — SSR-разметка, JSON-LD + текст карточки"


__all__ = ["Book", "InkStoryClient", "InkStoryError", "TOTAL_PAGES", "DEFAULT_DELAY"]
