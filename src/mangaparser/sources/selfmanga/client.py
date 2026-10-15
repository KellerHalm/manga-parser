"""SelfManga (1.selfmanga.live) — русский сайт авторской манги, SSR-разметка.

Каталога с данными в JSON нет: в оболочке сайта объявлен `SERVER_API_URL = '/api'`,
но этот префикс отдаёт 404, а весь контент приходит в HTML. Работа идёт с
публичными страницами:

| Что           | Запрос                                              |
|---------------|-----------------------------------------------------|
| каталог       | `/list?sortType=<сортировка>&offset=N` (50 на стр.)  |
| размер базы   | «Всего N» в заголовке `/list`                       |
| поиск         | `/search/advancedResults?q=<запрос>&offset=N`        |
| подсказки     | `/search/suggestion?query=` — JSON, быстрый поиск   |
| карточка      | `/<slug>`                                           |
| главы         | разметка `/<slug>` (`tr.item-row`), пагинации нет    |
| страницы главы| `/<slug>/volV/N` -> `rm_h.readerInit(...)`           |

Что стоит знать до использования:

* тайтл адресуется транслитерированным slug (`/djodjo__vpered_legenda_`), а не
  числовым id — id есть только в разметке (`div.tile.el_9117`, `data-id` списка
  глав) и он же используется для дедупликации;
* каталог отдаёт больше, чем видно без наведения мыши: в каждую плитку вложен
  попап с оригинальным названием, полным списком жанров, описанием и автором,
  поэтому отдельный `enrich` нужен только ради статусов, персон и статистики;
* `robots.txt` требует `Crawl-delay: 1` и `Request-rate: 1/1`, поэтому пауза по
  умолчанию здесь больше, чем у остальных источников, а `/internal/*` из
  robots.txt не обходится вовсе — только публичные страницы;
* сортировка `name` на сайте сломана: первая страница приходит, вторая уже
  пустая, поэтому обход каталога по ней невозможен (см. `SORTS`);
* страницы глав отдаёт не всегда: список картинок лежит в разметке ридера
  (`rm_h.readerInit`), а главы 18+ и ещё не опубликованные гостю открываются
  как карточка тайтла — без картинок, поэтому `chapter_pages()` вернёт пусто.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from ...core.http import Http, SourceError, Throttle, build_session
from ...core.models import Item
from ...core.models import chapter_field as _get
from ...core.ports import Checkpoint
from . import parsing

SITE = "https://1.selfmanga.live"
CATALOG_URL = f"{SITE}/list"
SEARCH_URL = f"{SITE}/search/advancedResults"
SUGGEST_URL = f"{SITE}/search/suggestion"
BOOK_URL = f"{SITE}/{{slug}}"
READER_URL = f"{SITE}/{{slug}}/vol{{vol}}/{{num}}"

PER_PAGE = 50  # ровно столько плиток отдаёт сервер, независимо от --count
DEFAULT_DELAY = 1.0  # robots.txt: Crawl-delay 1, Request-rate 1/1

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Referer": f"{SITE}/",
}

#: Сортировки сайта: имя из CLI -> значение параметра sortType.
SORTS: dict[str, str] = {
    "": "POPULARITY",
    "popularity": "POPULARITY",
    "created": "created",
    "updated": "updated",
    "rating": "rating",
    #: Сортировка по названию на сайте сломана: `/list?sortType=name&offset=50`
    #: возвращает пустую страницу. Оставлена для полноты, обход по ней не идёт.
    "name": "name",
}


class SelfMangaError(SourceError):
    """Ошибка SelfManga: сеть, 404 или неожиданный формат разметки."""


def slug_of(key: str) -> str:
    """
    Приводит любой ввод к slug тайтла.

    Принимает и `djodjo__vpered_legenda_`, и хвост URL, и полный адрес,
    и ссылку на главу — в последних случаях берётся первый сегмент пути.
    """
    tail = str(key or "").strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")
    if "://" in tail or tail.startswith("/"):
        tail = tail.replace(SITE, "", 1).lstrip("/")
    return tail.split("/")[0] if tail else ""


def chapter_url(key: str, vol: int | str | None = None, num: int | str | None = None) -> str:
    """
    Адрес главы для ридера.

    Если переданы `vol` и `num`, адрес собирается по шаблону сайта; иначе
    `key` считается готовым адресом — так `url` из `chapters()` можно
    передать как есть, не разбирая его обратно.
    """
    if vol is not None and num is not None:
        return READER_URL.format(slug=slug_of(key), vol=vol, num=num)

    tail = str(key or "").strip()
    if tail.startswith("http"):
        return tail
    slug = slug_of(tail)
    if not slug or "/vol" not in tail:
        raise SelfMangaError(f"Не удалось извлечь адрес главы: {key!r}. Ожидался /<slug>/volV/N")
    return f"{SITE}/{tail.lstrip('/')}"


@dataclass
class Manga(Item):
    """Тайтл SelfManga в нормализованном виде."""

    id: int
    title: str
    url: str
    slug: str
    #: Оригинальное/английское название (в каталоге — из попапа плитки).
    alt_title: str | None = None
    #: Полный список названий из карточки (включая оригинальное).
    alt_titles: list[str] = field(default_factory=list)
    cover: str | None = None
    #: Размытая обложка полного размера — подходит для фона страницы.
    wallpaper: str | None = None
    description: str | None = None
    genres: list[str] = field(default_factory=list)
    #: Метки вроде «Начинающий», «Сэндвич» — отделены от жанров.
    tags: list[str] = field(default_factory=list)
    rating: float | None = None
    votes: int | None = None
    story_rating: float | None = None
    characters_rating: float | None = None
    world_rating: float | None = None
    chapters: int | None = None
    status: str | None = None
    #: Технический код статуса: PROGRESS, FINISHED, CANCELED, ...
    production_status: str | None = None
    volumes: int | None = None
    release_frequency: str | None = None
    age_rating: str | None = None
    #: Тип тайтла: Манга, Додзинси, Вебтун, Комикс.
    category: str | None = None
    year: int | None = None
    #: Число огоньков в карточке (0-3) — грубый показатель популярности.
    popularity: int | None = None
    author: str | None = None
    #: Персоны с ролями строками «Автор: Доктор Моро».
    people: list[str] = field(default_factory=list)
    #: Счётчики читателей строками «В процессе: 18».
    stats: list[str] = field(default_factory=list)

    source = "selfmanga"

    @property
    def uid(self) -> str:
        return str(self.id)

    @property
    def key(self) -> str:
        """Ключ для details()/chapters() — slug тайтла."""
        return self.slug or self.url.rstrip("/").rsplit("/", 1)[-1]

    @classmethod
    def from_tile(cls, row: dict[str, Any]) -> Manga:
        """Запись из разбора плитки каталога или результатов поиска."""
        return cls(
            id=int(row.get("id") or 0),
            title=row.get("title") or "",
            url=row.get("url") or "",
            slug=row.get("slug") or "",
            alt_title=row.get("alt_title"),
            cover=row.get("cover"),
            description=row.get("description"),
            genres=list(row.get("genres") or []),
            rating=row.get("rating"),
            chapters=row.get("chapters"),
            author=row.get("author"),
        )

    @classmethod
    def from_card(cls, row: dict[str, Any]) -> Manga:
        """Запись из разбора карточки: каталог плюс поля, которых в нём нет."""
        return cls(
            id=int(row.get("id") or 0),
            title=row.get("title") or "",
            url=row.get("url") or "",
            slug=row.get("slug") or "",
            alt_titles=list(row.get("alt_titles") or []),
            cover=row.get("cover"),
            wallpaper=row.get("wallpaper"),
            description=row.get("description"),
            genres=list(row.get("genres") or []),
            tags=list(row.get("tags") or []),
            rating=row.get("rating"),
            votes=row.get("votes"),
            story_rating=row.get("story_rating"),
            characters_rating=row.get("characters_rating"),
            world_rating=row.get("world_rating"),
            chapters=row.get("chapters"),
            status=row.get("status"),
            production_status=row.get("production_status"),
            volumes=row.get("volumes"),
            release_frequency=row.get("release_frequency"),
            age_rating=row.get("age_rating"),
            category=row.get("category"),
            year=row.get("year"),
            popularity=row.get("popularity"),
            author=row.get("author"),
            people=list(row.get("people") or []),
            stats=list(row.get("stats") or []),
        )


class SelfMangaClient:
    """Клиент каталога SelfManga."""

    name = "selfmanga"
    #: Модель записи источника — нужна для обогащения выгрузки.
    item_class = Manga
    dedupe_key = "id"
    key_hint = "slug"
    #: Карточка отдаёт заметно больше каталога: описания, статусы, персоны, статистика.
    enrichable = True

    def __init__(self, session: Any | None = None, *, delay: float = DEFAULT_DELAY) -> None:
        self.http = Http(session or build_session(headers=HEADERS), error=SelfMangaError)
        self.delay = delay

    # ---------- низкий уровень ----------

    def _html(self, url: str, params: dict[str, Any] | None = None) -> str:
        return self.http.get_text(url, params)

    @staticmethod
    def _clean(params: dict[str, Any]) -> dict[str, Any]:
        """Пустые параметры не отправляются: сайт трактует их как «по умолчанию»."""
        return {k: v for k, v in params.items() if v not in (None, "")}

    def _page(self, url: str, params: dict[str, Any]) -> list[Manga]:
        """Общий разбор страницы с плитками (каталог и поиск отдают одну разметку)."""
        return [Manga.from_tile(row) for row in parsing.catalog_page(self._html(url, params), SITE)]

    # ---------- публичный API ----------

    def catalog(self, page: int = 1, sort: str = "", **_: Any) -> list[Manga]:
        """
        Страница каталога. `sort` — ключ из `SORTS`.

        Пагинация серверная через `offset`: страница N начинается с
        (N - 1) * 50. Размер страницы сайт задаёт сам, поэтому `count`
        игнорируется — обход всё равно получает страницы целиком.
        """
        params = self._clean({
            "sortType": SORTS.get(str(sort).casefold(), SORTS[""]),
            "offset": (max(page, 1) - 1) * PER_PAGE if page > 1 else "",
        })
        return self._page(CATALOG_URL, params)

    def search(self, query: str, page: int = 1, **_: Any) -> list[Manga]:
        """
        Серверный поиск по фрагменту названия.

        Эндпоинт тот же, что и у формы расширенного поиска, поэтому ответ —
        те же плитки, что и в каталоге. Страниц может быть много (по 50
        результатов), пустая страница означает конец выдачи.
        """
        if not str(query).strip():
            raise SelfMangaError("Пустой поисковый запрос: укажите фрагмент названия")
        params = self._clean({
            "q": query,
            "offset": (max(page, 1) - 1) * PER_PAGE if page > 1 else "",
        })
        return self._page(SEARCH_URL, params)

    def suggest(self, query: str, types: str | list[str] = "") -> list[dict[str, Any]]:
        """
        Подсказки поиска — единственный JSON-эндпоинт сайта.

        Быстрый (один запрос) и отдаёт `elementId.federationUID`, но только
        название, обложку, альтернативные имена и автора — поэтому основной
        путь поиска остаётся на `search()`.
        """
        if isinstance(types, (list, tuple)):
            types = ",".join(str(t) for t in types)
        data = self.http.get_json(SUGGEST_URL, self._clean({"query": query, "types": types}))
        return list(data.get("suggestions") or []) if isinstance(data, dict) else []

    def details(self, key: str) -> Manga:
        """Карточка тайтла: описание, статусы, персоны, теги, статистика."""
        slug = slug_of(key)
        if not slug:
            raise SelfMangaError(f"Не удалось извлечь slug тайтла из ключа: {key!r}")
        page = self._html(BOOK_URL.format(slug=slug))
        fields = parsing.details_fields(page, slug, SITE)
        if not fields.get("title"):
            raise SelfMangaError(f"Не удалось разобрать страницу тайтла: {slug}")
        return Manga.from_card(fields)

    def chapters(self, key: str) -> list[dict[str, Any]]:
        """Список глав — лежит в разметке карточки, отдельного запроса не нужно."""
        slug = slug_of(key)
        if not slug:
            raise SelfMangaError(f"Не удалось извлечь slug тайтла из ключа: {key!r}")
        return parsing.chapters(self._html(BOOK_URL.format(slug=slug)), SITE)

    def chapter_pages(self, key: str, chapter: Any, **_: Any) -> list[str]:
        """
        Адреса картинок главы — для загрузки изображений.

        Адрес главы берётся из самой записи (`url` из `chapters()`), а если
        её нет — собирается из `key` и номера главы.

        Список страниц лежит в разметке ридера (`rm_h.readerInit`), и сайт
        отдаёт его не всегда: главы 18+ и главы, которые ещё не опубликованы,
        гостю открываются как карточка тайтла — без вызова `readerInit` и без
        картинок. Тогда возвращается пустой список: это ограничение сайта, а не
        неудачный разбор.
        """
        url = _get(chapter, "url")
        url = chapter_url(url) if url else chapter_url(key, _get(chapter, "vol"), _get(chapter, "num"))
        return parsing.reader_pages(self._html(url))

    def stats(self) -> int:
        """Всего тайтлов в базе — нужно, чтобы знать размер обхода."""
        return parsing.catalog_total(self._html(CATALOG_URL)) or 0

    def total_pages(self) -> int:
        """Число страниц каталога по строке «Всего N»."""
        return (self.stats() + PER_PAGE - 1) // PER_PAGE

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
    ) -> Iterator[Manga]:
        """
        Обход каталога с JSONL-чекпоинтом: переживает краш и 429.

        Пустая страница или страница без новых id означает конец каталога.
        При ошибке пауза удваивается (до 60 с) и страница повторяется.
        """
        throttle = Throttle(delay if delay is not None else self.delay)
        page = 1
        seen: set[int] = set()
        while max_pages is None or page <= max_pages:
            try:
                items = self.catalog(page=page, sort=sort)
            except SelfMangaError:
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


def build() -> SelfMangaClient:
    return SelfMangaClient()


def describe() -> tuple[str, str]:
    return "SelfManga", "1.selfmanga.live — SSR-разметка: каталог, поиск, карточка, главы"


__all__ = [
    "Manga", "SelfMangaClient", "SelfMangaError", "slug_of", "chapter_url",
    "SORTS", "PER_PAGE", "DEFAULT_DELAY",
]
