"""Desu.Me (desu.uno) — источник по открытым sitemap и карточкам тайтлов.

Сайт на XenForo, публичного JSON API нет: всё, что нужно, лежит в
HTML-разметке и микроразметке schema.org.

    /sitemap.php            — весь каталог: 7 056 карточек одним запросом
    /manga/<slug>.<id>/     — карточка: описание, жанры, авторы, рейтинг, главы
    /manga/?page=N          — пагинация каталога
    /manga/<slug>.<id>/volN/chN/rus — страница главы

Что сайт разрешает, а что нет, определяет устройство источника:

    * пагинация каталога, поиск с фильтрами и страницы глав запрещены в
      `robots.txt` (`/manga/?page=*`, `/manga/search/*`, `/manga/*/vol*/ch*/`),
      поэтому `catalog()` — это срез уже скачанного sitemap, а не обход
      страниц: весь каталог стоит один сетевой запрос;
    * `search()` работает локально по sitemap, как у Com-X: серверный поиск
      закрыт, а в sitemap лежит название из slug (транслитерация оригинального
      названия), поэтому по русскому названию поиск не находит;
    * карточка `/manga/<slug>.<id>/` открыта — по ней работают `details()`
      (описание, жанры, авторы, переводчики, рейтинг, просмотры) и
      `chapters()`: список глав целиком лежит на карточке, постраничного
      обхода глав не требуется;
    * часть тайтлов в sitemap отдаёт 451 («закрыто по требованию
      правообладателя») или 401: это не сбой обхода, а норма, поэтому такие
      записи `enrich` пропускает и идёт дальше.

    python main.py desu dump --csv        # 7 056 адресов, 1 запрос
    python main.py desu search "one piece"
    python main.py desu details 12        # id подставляется сам
    python main.py desu enrich --limit 0  # дозагрузка карточек всего каталога
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from ...core.http import Http, SourceError, build_session
from ...core.models import Item
from ...core.ports import Checkpoint
from ...core.text import to_int
from . import parsing

SITE = "https://desu.uno"
SITEMAP_URL = f"{SITE}/sitemap.php"
MANGA_URL = f"{SITE}/manga/{{key}}/"

PER_PAGE = 200  # sitemap отдаётся целиком, страница — это срез в памяти
DEFAULT_DELAY = 0.7  # карточка тяжелее списка: обход глав и enrich

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": f"{SITE}/",
}

#: Порядок обхода. В самом sitemap записи идут по id, от старых к новым,
#: поэтому порядок сайта получается разворотом, а по алфавиту сортируется
#: локально, без дополнительных запросов.
SORTS = {
    "": "Как в sitemap (по id)",
    "newest": "Сначала новые",
    "alphabet": "По алфавиту",
}

#: Ключ тайтла: `<slug>.<id>` — ровно то, чем карточка адресуется на сайте.
_KEY_RE = re.compile(r"[\w\-.]+\.\d+")


class DesuError(SourceError):
    """Ошибка Desu.Me: сеть, недоступная карточка или неожиданный формат."""


@dataclass
class Title(Item):
    """Тайтл Desu.Me в нормализованном виде."""

    id: int
    title: str
    url: str
    slug: str
    #: Оригинальное название — русское идёт в `title`, как в остальных
    #: источниках проекта (MangaLIB, ReManga, InkStory, BuZuManga).
    alt_title: str | None = None
    #: «Другие названия» карточки: японское, альтернативное, транслитерации.
    alt_titles: list[str] = field(default_factory=list)
    cover: str | None = None
    description: str | None = None
    genres: list[str] = field(default_factory=list)
    #: Собственные рубрики сайта — в списке они помечены «#».
    tags: list[str] = field(default_factory=list)
    rating: float | None = None
    votes: int | None = None
    year: int | None = None
    type: str | None = None
    status: str | None = None
    translation_status: str | None = None
    views: int | None = None
    #: Направление чтения («Справа налево»).
    direction: str | None = None
    authors: list[str] = field(default_factory=list)
    translators: list[str] = field(default_factory=list)
    updated_at: str | None = None

    source = "desu"

    @property
    def uid(self) -> str:
        return str(self.id)

    @property
    def key(self) -> str:
        """Ключ для details()/chapters() — `<slug>.<id>`."""
        return f"{self.slug}.{self.id}"

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Title:
        """Запись каталога из sitemap — срез без карточки."""
        return cls(
            id=int(row.get("id") or 0),
            title=row.get("title") or "",
            url=row.get("url") or "",
            slug=row.get("slug") or "",
            updated_at=row.get("updated_at"),
        )

    @classmethod
    def from_page(cls, ident: int, slug: str, page: str) -> Title:
        """Карточка тайтла: к срезу из sitemap добавляются описание и жанры."""
        russian, original = parsing.names(page)
        genres, tags = parsing.genres(page)
        return cls(
            id=ident,
            title=russian or original or "",
            url=MANGA_URL.format(key=f"{slug}.{ident}"),
            slug=slug,
            alt_title=original if original != russian else None,
            alt_titles=parsing.info_list(page, "Другие названия"),
            cover=parsing.cover(page),
            description=parsing.description(page),
            genres=genres,
            tags=tags,
            rating=parsing.rating(page),
            votes=parsing.votes(page),
            year=parsing.year(parsing.info_value(page, "Статус")),
            type=parsing.info_value(page, "Тип"),
            status=parsing.status(page, "Статус"),
            translation_status=parsing.status(page, "Перевод"),
            views=to_int(parsing.info_value(page, "Просмотров")),
            direction=parsing.info_value(page, "Читать"),
            authors=parsing.info_list(page, "Авторы"),
            translators=parsing.info_list(page, "Переводчики"),
        )


class DesuClient:
    """Клиент каталога Desu.Me."""

    name = "desu"
    #: Модель записи источника — нужна для обогащения выгрузки.
    item_class = Title
    #: Поле дедупликации при финализации выгрузки.
    dedupe_key = "id"
    #: Что нужно этому источнику для адресации карточки/глав.
    key_hint = "id"
    #: Карточка отдаёт больше каталога: описание, жанры, авторы, главы.
    enrichable = True

    def __init__(self, session: Any | None = None, *, delay: float = DEFAULT_DELAY) -> None:
        self.http = Http(session or build_session(headers=HEADERS), error=DesuError)
        self.delay = delay
        self._rows: list[dict[str, Any]] | None = None

    # ---------- низкий уровень ----------

    def _html(self, url: str) -> str:
        return self.http.get_text(url)

    def rows(self) -> list[dict[str, Any]]:
        """
        Все записи каталога из sitemap.

        Результат кэшируется на клиенте: sitemap один, а `catalog()` и
        `search()` берут данные именно отсюда — поэтому весь обход каталога
        стоит ровно один сетевой запрос.
        """
        if self._rows is None:
            self._rows = parsing.entries(self._html(SITEMAP_URL))
            if not self._rows:
                raise DesuError("Не удалось прочитать sitemap")
        return self._rows

    # ---------- ключ тайтла ----------

    def key_of(self, key: str) -> str:
        """
        Приводит любой ввод к ключу источника — `<slug>.<id>`.

        Понимает полный URL (`https://desu.uno/manga/one-piece.12/`), ключ из
        выгрузки (`12`), голый slug (`one-piece`) и сам ключ. Всё, кроме
        готового `slug.id`, достраивается по sitemap — так ввод вручную
        (`python main.py desu details 12`) работает без угадывания slug.
        """
        raw = str(key).strip().strip("/").rsplit("/", 1)[-1]
        if not _KEY_RE.fullmatch(raw):
            if raw.isdigit():
                ident = int(raw)
                if (row := self._find(lambda r: r["id"] == ident)) is None:
                    raise DesuError(f"Тайтл не найден в sitemap: {key}")
                raw = f"{row['slug']}.{ident}"
            elif raw and (row := self._find(lambda r: r["slug"] == raw)):
                raw = f"{row['slug']}.{row['id']}"
        if not _KEY_RE.fullmatch(raw):
            raise DesuError(f"Не удалось разобрать ключ тайтла: {key!r}")
        return raw

    def _find(self, match: Callable[[dict[str, Any]], bool]) -> dict[str, Any] | None:
        return next((row for row in self.rows() if match(row)), None)

    # ---------- публичный API ----------

    def catalog(self, page: int = 1, count: int = PER_PAGE, sort: str = "") -> list[Title]:
        """Страница каталога — срез уже скачанного sitemap, без новых запросов."""
        return [Title.from_row(r) for r in self._slice(page, count, sort)]

    def search(self, query: str, page: int = 1, count: int = PER_PAGE) -> list[Title]:
        """
        Поиск по скачанному sitemap, а не по серверу.

        Серверный `/manga/search/` запрещён в `robots.txt`. В sitemap лежит
        только название из slug, поэтому сравнение идёт и по slug, и по
        названию: искать нужно по оригинальному названию, а не по русскому.
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

        Страница может оказаться недоступной (451 — закрыто по требованию
        правообладателя, 401 — нужен вход); на такой странице блока с
        характеристиками нет, и это ошибка источника, а не пустая карточка.
        """
        ident, slug = self._ident_slug(key)
        page = self._html(MANGA_URL.format(key=f"{slug}.{ident}"))
        if not parsing.info_regions(page):
            raise DesuError(f"Карточка недоступна: {slug}.{ident}")

        title = Title.from_page(ident, slug, page)
        # дата обновления есть только в sitemap — карточка её не публикует
        if row := self._find(lambda r: r["id"] == ident):
            title.updated_at = row["updated_at"]
        return title

    def chapters(self, key: str) -> list[dict[str, Any]]:
        """Главы тайтла — весь список лежит на карточке, одним запросом."""
        ident, slug = self._ident_slug(key)
        return parsing.chapters(self._html(MANGA_URL.format(key=f"{slug}.{ident}")), SITE)

    def stats(self) -> int:
        """Всего тайтлов в каталоге."""
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

        Сетевых запросов один (сам sitemap), поэтому пауза между страницами
        не нужна: `delay` ждёт только пока sitemap реально качается с сервера.
        Прогресс всё равно пишется в чекпоинт — из него потом собираются
        JSON/CSV и офлайн-индекс.
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

    def _ident_slug(self, key: str) -> tuple[int, str]:
        """Ключ источника -> (id, slug) для адреса карточки."""
        raw = self.key_of(key)
        slug, _, ident = raw.rpartition(".")
        return int(ident), slug

    def _slice(self, page: int, count: int, sort: str) -> Sequence[dict[str, Any]]:
        rows: Sequence[dict[str, Any]] = self.rows()
        if sort == "alphabet":
            rows = sorted(rows, key=lambda r: r["title"].casefold())
        elif sort == "newest":
            rows = sorted(rows, key=lambda r: r["id"], reverse=True)
        start = (max(page, 1) - 1) * count
        return list(rows[start: start + count])


def build() -> DesuClient:
    return DesuClient()


def describe() -> tuple[str, str]:
    return "Desu.Me", "desu.uno — sitemap.php + карточки: описание, жанры, авторы, главы"


__all__ = [
    "Title", "DesuClient", "DesuError", "SORTS", "PER_PAGE", "DEFAULT_DELAY", "SITEMAP_URL",
]