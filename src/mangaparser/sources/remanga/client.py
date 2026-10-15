"""ReManga — источник на API v2/v3 (api.remanga.org).

Эндпоинты извлечены из бандлов remanga.org (Next.js chunks), а не угаданы:

    /api/v3/titles/?page=&count=       — лента новинок (count <= 50), ~400 тайтлов
    /api/v2/search/catalog/?page=&count= — полный каталог (count <= 30)
    /api/v2/search/?query=&page=        — серверный поиск, meta.total_items/total_pages
    /api/v2/titles/{dir}/               — карточка (поиск по dir)
    /api/v2/titles/{dir}/chapters/available/ — главы

Каталог и карточка отдают разный объём: список — это срез (название, обложка,
рейтинг, статус), а `/titles/{dir}/` добавляет описание, жанры, авторов,
издательства, возрастное ограничение и заметку. Поэтому полные данные берутся
командой `enrich`: `dump` быстрый, `enrich` медленный, но полный.

Хост api.remanga.org за DDoS-Guard редиректит (302) на основной домен,
поэтому включён allow_redirects.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from ...core.http import Http, SourceError, Throttle, build_session
from ...core.models import Item, extract_pages
from ...core.ports import Checkpoint
from ...core.text import label, strip_tags, to_float, to_int

API = "https://api.remanga.org"
SITE = "https://remanga.org"
TITLES_V3 = f"{API}/api/v3/titles/"
CATALOG_V2 = f"{API}/api/v2/search/catalog/"
SEARCH_V2 = f"{API}/api/v2/search/"

MAX_COUNT = 50      # сервер отвечает 422 при count > 50
CATALOG_COUNT = 30  # для catalog эндпоинта сервер молча отдаёт 0 при count > 30
DEFAULT_DELAY = 0.3

HEADERS = {
    "Accept": "application/json",
    "Referer": f"{SITE}/",
    "Origin": SITE,
}

#: Типы в поле `creators`: 1 и 4 — авторы и художники, 2 и 3 — издательства
#: и журналы. Проверено по выборке названий (Shueisha/Viz/Азбука -> 3,
#: Kishimoto/Oda -> 4, ONE/ISSHIKI Ichika -> 1, Micro Magazine -> 2).
CREATOR_ORGS = frozenset({2, 3})


class ReMangaError(SourceError):
    """Ошибка ReManga: сеть, 404/422 или неожиданный формат ответа."""


@dataclass
class Title(Item):
    """Тайтл ReManga в нормализованном виде."""

    id: int
    name: str
    url: str
    dir: str
    secondary_name: str | None = None
    another_name: str | None = None
    cover: str | None = None
    type: str | None = None
    status: str | None = None
    translate_status: str | None = None
    year: int | None = None
    rating: float | None = None
    votes: int | None = None
    views: int | None = None
    bookmarks: int | None = None
    chapters: int | None = None
    is_licensed: bool = False
    is_yaoi: bool = False
    is_erotic: bool = False
    description: str | None = None
    genres: list[str] = field(default_factory=list)
    #: Дополнительные рубрики сайта (не жанры, а «Культивация», «В цвете»...).
    categories: list[str] = field(default_factory=list)
    #: Авторы и художники: 1 и 4 в типе creators.
    authors: list[str] = field(default_factory=list)
    #: Издательства и журналы: 2 и 3 в типе creators.
    publishers: list[str] = field(default_factory=list)
    age_limit: str | None = None
    note: str | None = None
    has_anime: bool | None = None
    fresh_rating: float | None = None
    wallpaper: str | None = None

    source = "remanga"

    @property
    def title(self) -> str:
        return self.name

    @property
    def uid(self) -> str:
        return str(self.id)

    @property
    def key(self) -> str:
        """Ключ для details()/chapters() — dir из URL."""
        return self.dir or self.url.rstrip("/").rsplit("/", 1)[-1]

    def __post_init__(self) -> None:
        # обложки и обои часто приходят относительным путём
        for name in ("cover", "wallpaper"):
            value = getattr(self, name)
            if value and value.startswith("/"):
                setattr(self, name, f"{SITE}{value}")

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Title:
        """
        Запись из любого ответа API — списочного и полной карточки.

        Списочный ответ содержит срез (название, обложка, рейтинг), а
        `/titles/{dir}/` добавляет описание, жанры, авторов и издателей:
        ключи, которых нет в списке, просто остаются пустыми.
        """
        cover = raw.get("cover") or {}
        wallpaper = raw.get("wallpaper") or {}
        dir_ = raw.get("dir") or ""
        authors, publishers = _split_creators(raw.get("creators") or [])
        return cls(
            id=raw.get("id", 0),
            name=raw.get("main_name") or "",
            url=f"{SITE}/{dir_}",
            dir=dir_,
            secondary_name=raw.get("secondary_name"),
            another_name=raw.get("another_name"),
            cover=cover.get("high") or cover.get("mid") or cover.get("low"),
            type=label(raw.get("type"), "name"),
            status=label(raw.get("status"), "name"),
            translate_status=label(raw.get("translate_status"), "name"),
            year=to_int(raw.get("issue_year")),
            rating=to_float(raw.get("avg_rating")),
            votes=to_int(raw.get("total_votes")),
            views=to_int(raw.get("total_views")),
            bookmarks=to_int(raw.get("count_bookmarks")),
            chapters=to_int(raw.get("count_chapters")),
            is_licensed=bool(raw.get("is_licensed")),
            is_yaoi=bool(raw.get("is_yaoi")),
            is_erotic=bool(raw.get("is_erotic")),
            description=strip_tags(raw.get("description") or raw.get("summary") or "") or None,
            genres=[label(g, "name") for g in (raw.get("genres") or [])],
            categories=[label(g, "name") for g in (raw.get("categories") or [])],
            authors=authors,
            publishers=publishers,
            age_limit=label(raw.get("age_limit"), "name"),
            note=strip_tags(raw.get("note") or "") or None,
            has_anime=raw.get("has_anime"),
            fresh_rating=to_float(raw.get("fresh_rating")),
            wallpaper=wallpaper.get("high") or wallpaper.get("mid") or wallpaper.get("low"),
        )


def _split_creators(raw: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """`creators` -> (авторы/художники, издательства/журналы)."""
    people: list[str] = []
    orgs: list[str] = []
    for creator in raw:
        name = creator.get("name")
        if not name:
            continue
        (orgs if creator.get("type") in CREATOR_ORGS else people).append(name)
    return people, orgs


class ReMangaClient:
    """Клиент каталога ReManga."""

    name = "remanga"
    #: Модель записи источника — нужна для обогащения выгрузки.
    item_class = Title
    dedupe_key = "id"
    key_hint = "dir"

    def __init__(self, session: Any | None = None, *, delay: float = DEFAULT_DELAY) -> None:
        # 429 обрабатываем сами в iter_catalog (удвоение паузы до 60 с),
        # встроенный ретрай здесь просто исчерпывается и роняет обход
        self.http = Http(
            session or build_session(headers=HEADERS, retry_statuses=(500, 502, 503, 504)),
            error=ReMangaError,
        )
        self.delay = delay

    # ---------- низкий уровень ----------

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.http.get_json(url, params)

    @staticmethod
    def _path(dir_: str) -> str:
        # dir содержит спецсимволы (<29.04.2026>naruto_), нужен percent-encoding
        return quote(dir_, safe="")

    # ---------- каталог ----------

    def catalog(self, page: int = 1, count: int = CATALOG_COUNT) -> list[Title]:
        """Страница полного каталога."""
        data = self._get(CATALOG_V2, {"page": page, "count": min(count, CATALOG_COUNT)})
        return [Title.from_api(r) for r in data.get("results", [])]

    def latest(self, page: int = 1, count: int = MAX_COUNT) -> list[Title]:
        """Лента новинок — обновляется быстрее каталога."""
        data = self._get(TITLES_V3, {"page": page, "count": min(count, MAX_COUNT)})
        return [Title.from_api(r) for r in data.get("results", [])]

    def search(self, query: str, page: int = 1) -> list[Title]:
        """Серверный поиск."""
        return self._search(query, page)[0]

    def search_meta(self, query: str, page: int = 1) -> dict[str, Any]:
        """Метаданные поиска: total_items, total_pages, page."""
        return self._search(query, page)[1]

    def _search(self, query: str, page: int) -> tuple[list[Title], dict[str, Any]]:
        data = self._get(SEARCH_V2, {"query": query, "page": page})
        return [Title.from_api(r) for r in data.get("results", [])], data.get("meta", {})

    def details(self, dir_: str) -> Title:
        """
        Полная карточка тайтла.

        Ответ приходит то с обёрткой `data`, то без неё — оба варианта
        приводим к `Title`, чтобы карточка дополняла запись выгрузки
        теми же полями, что и каталог (описание, жанры, авторы, издатели).
        """
        payload = self._get(f"{API}/api/v2/titles/{self._path(dir_)}/")
        data = payload.get("data")
        return Title.from_api(data if isinstance(data, dict) else payload)

    def chapters(self, dir_: str) -> list[dict[str, Any]]:
        payload = self._get(f"{API}/api/v2/titles/{self._path(dir_)}/chapters/available/")
        data = payload.get("data") or payload.get("results")
        return data if isinstance(data, list) else []

    def chapter_pages(self, key: str, chapter: Any, **_: Any) -> list[str]:
        """
        URL страниц главы — сети не нужно.

        ReManga отдаёт список картинок прямо в главе (`content`), поэтому
        `key` и не используется: разбор двухуровневой структуры в ядре.
        """
        return extract_pages(chapter)

    # ---------- обход каталога ----------

    def iter_catalog(
        self,
        *,
        delay: float | None = None,
        max_pages: int | None = None,
        count: int = CATALOG_COUNT,
        checkpoint: Checkpoint | None = None,
        on_page: Callable[..., None] | None = None,
        **_: Any,
    ) -> Iterator[Title]:
        """
        Обход полного каталога через серверную пагинацию next/previous.

        Устойчив к 429: при rate-limit пауза удваивается (до 60 с) и страница
        повторяется. Сервер ограничивает пагинацию 1000 страницами — это
        трактуется как конец каталога.
        """
        throttle = Throttle(delay if delay is not None else self.delay)
        page = 1
        seen: set[int] = set()
        while max_pages is None or page <= max_pages:
            try:
                titles, nxt = self._catalog_page(page, count)
            except ReMangaError as exc:
                if _is_end_of_catalog(exc):
                    if on_page:
                        on_page("end", page, str(exc))
                    return
                throttle.on_rate_limit()
                if on_page:
                    on_page("rate-limited", page)
                continue

            throttle.on_success()
            if not titles:
                if on_page:
                    on_page("empty", page)
                return

            fresh = [t for t in titles if t.id not in seen]
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

            if not nxt:
                return
            page += 1
            throttle.wait()

    def _catalog_page(self, page: int, count: int) -> tuple[list[Title], str | None]:
        data = self._get(CATALOG_V2, {"page": page, "count": min(count, CATALOG_COUNT)})
        return [Title.from_api(r) for r in data.get("results", [])], data.get("next")


def _is_end_of_catalog(exc: Exception) -> bool:
    """Сервер отвечает 422 «page must be less than 1000» — это конец каталога."""
    message = str(exc).lower()
    return "less than 1000" in message or "page" in message


def build() -> ReMangaClient:
    return ReMangaClient()


def describe() -> tuple[str, str]:
    return "ReManga", "api.remanga.org — JSON API v2/v3, каталог + новинки"


__all__ = ["Title", "ReMangaClient", "ReMangaError", "MAX_COUNT", "CATALOG_COUNT", "DEFAULT_DELAY"]
