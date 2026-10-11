"""MangaLIB — источник с публичным JSON API (api2.mangalib.me/api/manga).

Эндпоинты и параметры вытащены из фронтенда mangalib.me (build/assets/common-*.js),
а не угаданы:

    /api/manga?q=&page=&sort_by=&sort_type=  — поиск и каталог (60 записей на стр.)
    /api/manga/{slug}                        — карточка
    /api/manga/{slug}/chapters               — главы
    /api/manga/{slug}/chapter/{id}           — страницы главы (URL картинок)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ...core.http import Http, SourceError, Throttle, build_session
from ...core.models import Item
from ...core.ports import Checkpoint
from ...core.text import label, to_float, to_int

API = "https://api2.mangalib.me"
SITE = "https://mangalib.me"
CATALOG_URL = f"{API}/api/manga"
PAGE_SIZE = 60
DEFAULT_DELAY = 0.5

HEADERS = {
    "Accept": "application/json",
    "Referer": f"{SITE}/",
}

#: Допустимые значения sort_by (проверены запросом; другие дают 422)
SORT_FIELDS = {
    "rating_score": "По рейтингу",
    "created_at": "По дате добавления",
}


class MangaLibError(SourceError):
    """Ошибка MangaLIB: сеть, 404/422 или неожиданный формат ответа."""


@dataclass
class Manga(Item):
    """Тайтл MangaLIB в нормализованном виде."""

    id: int
    name: str
    url: str
    eng_name: str | None = None
    slug: str | None = None
    cover: str | None = None
    rating: float | None = None
    votes: int | None = None
    status: str | None = None
    type: str | None = None
    release_date: str | None = None
    age: str | None = None
    is_licensed: bool | None = None
    description: str | None = None
    alt_titles: dict[str, str] = field(default_factory=dict)

    source = "mangalib"

    @property
    def title(self) -> str:
        return self.name

    @property
    def uid(self) -> str:
        return str(self.id)

    @property
    def key(self) -> str:
        """Ключ для details()/chapters() — slug из URL."""
        return self.slug or self.url.rstrip("/").rsplit("/", 1)[-1]

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> Manga:
        cover = raw.get("cover") or {}
        rating = raw.get("rating") or {}
        slug = raw.get("slug_url") or ""
        alt = {"original": raw.get("name"), "english": raw.get("eng_name")}
        return cls(
            id=raw.get("id", 0),
            name=raw.get("rus_name") or raw.get("name") or "",
            url=f"{SITE}/{slug}" if slug else "",
            eng_name=raw.get("eng_name"),
            slug=slug or None,
            cover=cover.get("default") or cover.get("thumbnail"),
            rating=to_float(rating.get("averageFormated") or rating.get("average")),
            votes=to_int(rating.get("votes")),
            status=label(raw.get("status")),
            type=label(raw.get("type")),
            release_date=raw.get("releaseDateString"),
            age=label(raw.get("ageRestriction")),
            is_licensed=raw.get("is_licensed"),
            description=raw.get("description"),
            alt_titles={k: v for k, v in alt.items() if v},
        )


class MangaLibClient:
    """Клиент каталога MangaLIB."""

    name = "mangalib"
    #: Модель записи источника — нужна для обогащения выгрузки.
    item_class = Manga
    #: Поле дедупликации при финализации выгрузки.
    dedupe_key = "id"
    #: Что нужно этому источнику для адресации карточки/глав.
    key_hint = "slug"

    def __init__(self, session: Any | None = None, *, delay: float = DEFAULT_DELAY) -> None:
        self.http = Http(session or build_session(headers=HEADERS), error=MangaLibError)
        self.delay = delay

    # ---------- низкий уровень ----------

    @staticmethod
    def _rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
        data = payload.get("data")
        if isinstance(data, dict) and "toast" in data:
            raise MangaLibError(data["toast"].get("message", "Ошибка API"))
        return data if isinstance(data, list) else []

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self.http.get_json(url, params)

    # ---------- публичный API ----------

    def catalog(self, page: int = 1, sort_by: str = "rating_score", **filters: Any) -> list[Manga]:
        params = _clean({"page": page, "sort_by": sort_by, "sort_type": "desc", **filters})
        return [Manga.from_api(r) for r in self._rows(self._get(CATALOG_URL, params))]

    def search(
        self, query: str, page: int = 1, sort_by: str = "rating_score", **filters: Any
    ) -> list[Manga]:
        """Серверный поиск — основной быстрый путь (1 запрос вместо обхода каталога)."""
        params = _clean({
            "q": query, "page": page, "sort_by": sort_by, "sort_type": "desc", **filters,
        })
        return [Manga.from_api(r) for r in self._rows(self._get(CATALOG_URL, params))]

    def details(self, slug: str) -> Manga:
        raw = self._get(f"{API}/api/manga/{slug}").get("data") or {}
        return Manga.from_api(raw)

    def chapters(self, slug: str) -> list[dict[str, Any]]:
        return self._rows(self._get(f"{API}/api/manga/{slug}/chapters"))

    def chapter_pages(self, slug: str, chapter_id: int) -> list[str]:
        """URL страниц главы (для загрузки изображений)."""
        pages: list[str] = []
        for row in self._rows(self._get(f"{API}/api/manga/{slug}/chapter/{chapter_id}")):
            img = row.get("img") or row.get("image") or {}
            url = (img.get("default") or img.get("original")) if isinstance(img, dict) else img
            if url:
                pages.append(url)
        return pages

    # ---------- обход каталога ----------

    def iter_catalog(
        self,
        *,
        delay: float | None = None,
        max_pages: int | None = None,
        checkpoint: Checkpoint | None = None,
        on_page: Callable[..., None] | None = None,
        **_: Any,
    ) -> Iterator[Manga]:
        """
        Постраничный обход каталога.

        Пустая страница = конец каталога. При 429 пауза удваивается (до 60 с),
        прогресс дописывается в checkpoint по мере обхода.
        """
        throttle = Throttle(delay if delay is not None else self.delay)
        page = 1
        seen: set[int] = set()
        while max_pages is None or page <= max_pages:
            try:
                items = self.catalog(page=page)
            except MangaLibError:
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
    return {k: v for k, v in params.items() if v not in (None, [], "")}


def build() -> MangaLibClient:
    return MangaLibClient()


def describe() -> tuple[str, str]:
    return "MangaLIB", "api2.mangalib.me — JSON API, 60 записей на страницу"


__all__ = ["Manga", "MangaLibClient", "MangaLibError", "SORT_FIELDS", "DEFAULT_DELAY"]
