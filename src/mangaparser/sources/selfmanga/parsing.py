"""Разбор HTML SelfManga (1.selfmanga.live) — сайт рендерится на сервере.

Публичного JSON API с данными каталога нет: переменная `SERVER_API_URL = '/api'`
в оболочке ведёт в 404, а весь нужный контент приходит в HTML. Поэтому здесь
используются четыре источника данных, все — из ответа сервера:

    1. `/list` (каталог)         — плитки `div.tile.el_<id>`: название, обложка,
                                   рейтинг, число глав, жанры, автор, описание;
    2. `/search/advancedResults` — та же разметка плиток, поэтому разбор один;
    3. `/<slug>` (карточка)      — блоки `cr-*`: описание, статусы, персоны, теги,
                                   оценки, статистика читателей и полный список
                                   глав (`tr.item-row`);
    4. `/<slug>/volV/N` (ридер)  — вызов `rm_h.readerInit(...)` со списком страниц.

Разбор вынесен отдельно от HTTP-клиента, чтобы покрыть его тестами на фикстурах
без обращения к сети.
"""

from __future__ import annotations

import html
import re
from typing import Any

from ...core.text import strip_tags, to_compact_int, to_float, to_int

# ---------- каталог / поиск ----------

#: Плитка каталога: id тайтла лежит в классе (`class="tile col-sm-6 el_9117"`).
_TILE_RE = re.compile(r'<div class="tile[^"]*\bel_(\d+)"[^>]*>')

#: Ссылка с названием тайтла внутри плитки (в каталоге она одна).
_TITLE_LINK_RE = re.compile(
    r'<h3>\s*<a href="/(?P<slug>[^"#?]+)"[^>]*?(?:title="(?P<title>[^"]*)")?[^>]*>\s*(?P<text>[^<]*)',
    re.S,
)

#: Обложка: маленькое превью `_p.webp`, грузится лениво через data-original.
#: Кавычки в разметке сайта одинарные, но полагаться на это нельзя.
_COVER_RE = re.compile(r"data-original=['\"]([^'\"]+)['\"]")

#: Рейтинг плитки лежит в title блока compact-rate. Внутри плитки их два —
#: внешний и такой же в попапе, поэтому берётся первый.
_RATE_RE = re.compile(r'<div class="compact-rate[^"]*"\s+title="([^"]+)"')

#: Бейдж с числом глав.
_CHAPTERS_RE = re.compile(r'class="badge badge-secondary amount-badge">\s*(\d+)')

#: Оригинальное/английское название — заголовок попапа.
_POPOVER_TITLE_RE = re.compile(r"<h5>([^<]*)</h5>")

#: Жанры плитки: полный список, в отличие от двух-трёх в tile-info.
_GENRE_RE = re.compile(r"<span class='badge badge-dark elem_genre[^']*'>([^<]*)</span>")

#: Описание тайтла в попапе.
_DESCRIPTION_RE = re.compile(r'<div class="manga-description">\s*(.*?)\s*</div>', re.S)

#: Автор плитки: ссылка на страницу персоны с классом person-link.
_AUTHOR_RE = re.compile(r'<a href="/list/person/[^"]*"[^>]*class="person-link"[^>]*>([^<]*)</a>')

#: «Всего 3410» — размер базы, из него считается число страниц обхода.
_TOTAL_RE = re.compile(r"<h5>\s*Всего\s*([\d\s\u00a0]+?)\s*</h5>")

# ---------- карточка ----------

#: Название тайтла.
_TITLE_H1_RE = re.compile(r'<h1 class="cr-hero-names__main[^"]*">(.*?)</h1>', re.S)

#: Описание в meta всегда есть и уже в виде чистого текста.
_DESC_META_RE = re.compile(r'<meta itemprop="description" content="([^"]*)"')

#: Запасной вариант описания — сам блок с тегами <p>.
_DESC_BLOCK_RE = re.compile(
    r'<div class="cr-description__content"[^>]*>(.*?)</div>\s*<button', re.S
)

#: Обложка (первый кадр карусели) и её размытый фон. Первый кадр отдаётся
#: с `src`, остальные — ленивые, с `data-src`, поэтому атрибут ищется в теге.
_COVER_TAG_RE = re.compile(r'(<img[^>]*class="cr-hero-poster__img[^"]*"[^>]*>)')
_IMG_SRC_RE = re.compile(r'\ssrc="([^"]+)"')
_IMG_DATA_SRC_RE = re.compile(r'\sdata-src="([^"]+)"')
_WALLPAPER_RE = re.compile(r'class="cr-hero-overlay__bg[^"]*"[^>]*data-bg="([^"]+)"')

#: Основная оценка: помечена data-rating-status, у оценки пользователя его нет.
_RATING_RE = re.compile(
    r'<strong class="cr-hero-rating__value"[^>]*data-rating-status="positive"[^>]*>([\d.,]+)</strong>'
)
_VOTES_RE = re.compile(r'<div class="cr-hero-rating__text">([^<]*)</div>')

#: Дополнительные оценки: сюжет / персонажи / мир.
_SUB_RATING_RE = re.compile(
    r'data-rating-type="(\w+)"[^>]*>\s*(?:<i[^>]*></i>\s*)?'
    r'<span class="cr-hero-rating__value">([^<]*)</span>'
)

#: Возрастное ограничение («PG», «R18») и тип тайтла (манга / додзинси / вебтун).
_LIMITATION_RE = re.compile(r'href="/list/limitation/[^"]+"[^>]*>([^<]+)</a>', re.S)
_CATEGORY_RE = re.compile(r'href="/list/category/[^"]+"[^>]*>([^<]+)</a>', re.S)

#: Год: «2024 г.».
_YEAR_RE = re.compile(r'href="/list/year/(\d{4})"')

#: Популярность: сайт рисует от одного до трёх огоньков.
_POPULARITY_RE = re.compile(r'<i class="fas fa-fire-alt"></i>')
_POPULARITY_END = 'cr-hero-names'

#: Характеристики карточки: «Выпуск -> Продолжается», «Томов -> 1».
_INFO_ITEM_RE = re.compile(
    r'<div class="cr-info-details-item[^"]*">\s*'
    r'<div class="cr-info-details-item__title">(?P<title>[^<]*)</div>\s*'
    r'<div class="cr-info-details-item__content">(?P<value>.*?)</div>\s*</div>',
    re.S,
)

#: Технический код статуса выпуска (PROGRESS, FINISHED, ...).
_PRODUCTION_RE = re.compile(r'data-production-status="(\w+)"[^>]*>([^<]*)<')

#: Жанры (`/list/genre/...`) и метки («Начинающий», `/list/another/...`).
_TAG_RE = re.compile(
    r'<a class="creation-element-tags__item(?P<misc>\s+creation-element-tags__item--misc)?"\s*'
    r'href="/list/(?P<kind>genre|another|tag)/(?P<code>[^"]+)"[^>]*>\s*<span>(?P<name>[^<]*)</span>'
)

#: Персоны с ролями (автор, художник, сценарист, ...).
_PERSON_MARK_RE = re.compile(r'<div class="cr-main-person-item\b')
_PERSON_ROLE_RE = re.compile(r'cr-main-person-item__role[^>]*>(.*?)</div>', re.S)
_PERSON_NAME_RE = re.compile(
    r'cr-main-person-item__name[^>]*>\s*(?:<a href="(?P<href>[^"]*)"[^>]*>)?\s*(?P<name>[^<]*)',
    re.S,
)

#: Другие названия лежат в модальном окне: подпись + значение.
_ALT_NAMES_RE = re.compile(r'<h5 class="m-0">([^<]*)</h5>\s*<div class="py-1">(.*?)</div>', re.S)
_ALT_NAMES_END = 'class="tab-content'

#: Статистика читателей по типам (в процессе, отложено, готово, ...).
_STAT_RE = re.compile(
    r'<div class="cr-stats-item" data-type-id="([^"]+)">.*?'
    r'cr-stats-item__name[^>]*>([^<]*)<.*?'
    r'cr-stats-item__value">([^<]*)<',
    re.S,
)

#: Число глав дублируется в счётчике вкладки «Главы». Подпись вкладки лежит
#: во вложенном span, поэтому берётся он, а не открывающий тег вкладки.
_TAB_LABEL_RE = re.compile(
    r'<span data-tab-text="Главы">[^<]*</span>\s*'
    r'<span class="cr-tabs-list__label">\s*(\d+)\s*</span>'
)

#: Внутренний id тайтла. Источники перебираются от надёжного к запасным:
#: список глав, служебная ссылка редактора, кнопка закладки.
_ID_RES = (
    re.compile(r'id="chapters-list"[^>]*data-id="(\d+)"'),
    re.compile(r"/internal/edit/edit/(\d+)"),
    re.compile(r'type="MANGA"[^>]*external-id="(\d+)"'),
)

# ---------- главы ----------

_CHAPTER_ROW_RE = re.compile(
    r'<tr class="item-row"[^>]*data-id="(\d+)"[^>]*data-vol="(\d+)"[^>]*data-num="(\d+)"[^>]*>'
    r"(.*?)</tr>",
    re.S,
)
_CHAPTER_LINK_RE = re.compile(
    r'<a href="(?P<href>[^"]+)"(?P<attrs>[^>]*)class="chapter-link[^"]*"[^>]*>(?P<text>.*?)</a>',
    re.S,
)
_CHAPTER_DATE_RE = re.compile(r'data-date="([^"]*)"\s+data-date-raw="([^"]*)"')
_CHAPTER_TITLE_ATTR_RE = re.compile(r'title="([^"]*)"')

#: Подпись главы начинается с номера: «5 - 20 Название». Номер уже лежит в
#: поле `number`, поэтому из названия он убирается, а полная подпись остаётся.
_CHAPTER_PREFIX_RE = re.compile(r"^\s*\d+\s*[-–—]\s*\d+\s*")

#: Адрес главы: /<slug>/vol<V>/<N>. Хвост `#comm_1&page=2` — якорь комментария.
_CHAPTER_URL_RE = re.compile(r"/vol(?P<vol>\d+)/(?P<num>\d+)")

# ---------- страницы главы ----------

#: rm_h.readerInit(info, pages, isWebMode, servers, isPlanned), где каждая
#: страница — это `[origin, '', 'путь?подпись', ширина, высота, url_не_vip]`.
#: Ридер берёт `origin + путь`, но если шестой элемент непустой и пользователь
#: не VIP — использует его, поэтому гостю отдаётся именно он.
_READER_MARK_RE = re.compile(r"rm_h\.readerInit\(")
_READER_PAGE_RE = re.compile(
    r"\[\s*['\"](?P<origin>[^'\"]*)['\"]\s*,\s*['\"][^'\"]*['\"]\s*,\s*"
    r"['\"](?P<path>[^'\"]+)['\"]\s*,\s*(?P<width>\d+)\s*,\s*(?P<height>\d+)\s*,\s*"
    r"['\"](?P<guest>[^'\"]*)['\"]\s*\]"
)


def text_of(raw: str | None) -> str | None:
    """Разметка -> текст; пустое значение становится None, а не пустой строкой."""
    value = html.unescape(strip_tags(raw or "")).replace("\xa0", " ").strip()
    return value or None


def _group(regex: re.Pattern[str], text: str, group: str | int = 1) -> str | None:
    """Группа первого совпадения готовой регулярки или None."""
    match = regex.search(text or "")
    return match.group(group) if match else None


def _blocks(page: str, marker: re.Pattern[str]) -> list[str]:
    """
    Разрезать разметку по маркеру: каждый блок — от маркера до следующего.

    Так inner-блоки (плитка каталога, карточка персоны) не захватывают
    соседние элементы, а вложенные поддеревья остаются целыми.
    """
    marks = list(marker.finditer(page or ""))
    return [
        page[mark.start(): marks[index + 1].start() if index + 1 < len(marks) else len(page)]
        for index, mark in enumerate(marks)
    ]


# ---------- каталог ----------

def tiles(page: str) -> list[tuple[int, str]]:
    """Плитки каталога как `(id, блок разметки)`."""
    marks = list(_TILE_RE.finditer(page or ""))
    blocks = _blocks(page, _TILE_RE)
    return [(int(mark.group(1)), block) for mark, block in zip(marks, blocks, strict=True)]


def tile_item(ident: int, block: str, site: str) -> dict[str, Any]:
    """
    Плитка каталога -> плоские поля модели.

    Всё берётся из попапа, который сайт кладёт в каждую плитку: там есть
    оригинальное название, полный список жанров и описание — то есть каталог
    отдает заметно больше, чем видно без наведения мыши.
    """
    link = _TITLE_LINK_RE.search(block)
    slug = link.group("slug") if link else ""
    title = text_of((link.group("title") if link else "") or (link.group("text") if link else ""))
    alt_title = text_of(_group(_POPOVER_TITLE_RE, block))
    genres = [g for g in (text_of(raw) for raw in _GENRE_RE.findall(block)) if g]

    return {
        "id": ident,
        "slug": slug,
        "url": f"{site}/{slug}" if slug else "",
        "title": title or alt_title or "",
        "alt_title": alt_title if alt_title and alt_title != title else None,
        "cover": _group(_COVER_RE, block),
        "description": text_of(_group(_DESCRIPTION_RE, block)),
        "genres": genres,
        "rating": to_float(_group(_RATE_RE, block)),
        "chapters": to_int(_group(_CHAPTERS_RE, block)),
        "author": text_of(_group(_AUTHOR_RE, block)),
    }


def catalog_page(page: str, site: str) -> list[dict[str, Any]]:
    """Страница каталога или результатов поиска -> список записей."""
    return [tile_item(ident, block, site) for ident, block in tiles(page)]


def catalog_total(page: str) -> int | None:
    """«Всего 3410» из заголовка каталога — размер базы."""
    return to_compact_int(_group(_TOTAL_RE, page))


# ---------- карточка ----------

def info_fields(page: str) -> dict[str, str]:
    """
    Блок характеристик карточки: «Выпуск -> Продолжается», «Томов -> 1».

    Блок встречается дважды (в герое и в мобильной вкладке) с одинаковыми
    значениями, поэтому повтор просто перезаписывает то же самое.
    """
    fields: dict[str, str] = {}
    for match in _INFO_ITEM_RE.finditer(page or ""):
        title, value = text_of(match.group("title")), text_of(match.group("value"))
        if title and value:
            fields[title] = value
    return fields


def tags(page: str) -> dict[str, list[str]]:
    """Жанры (`genres`) и метки вроде «Начинающий» (`tags`) — различаются по href."""
    genres: list[str] = []
    others: list[str] = []
    for match in _TAG_RE.finditer(page or ""):
        name = text_of(match.group("name"))
        if not name:
            continue
        target = others if match.group("kind") != "genre" or match.group("misc") else genres
        if name not in target:
            target.append(name)
    return {"genres": genres, "tags": others}


def persons(page: str) -> list[dict[str, str]]:
    """
    Персоны карточки с ролями: автор, художник, сценарист, ...

    Роль и имя лежат в соседних блоках, поэтому карточка режется по границам
    `cr-main-person-item`. Граница — точное совпадение слова: вложенный
    `cr-main-person-item__covers` под неё не подходит.
    """
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for block in _blocks(page, _PERSON_MARK_RE):
        name_match = _PERSON_NAME_RE.search(block)
        role = text_of(_group(_PERSON_ROLE_RE, block))
        name = text_of(name_match.group("name")) if name_match else None
        if not role or not name or (role, name) in seen:
            continue
        seen.add((role, name))
        href = name_match.group("href") or ""
        out.append({
            "role": role,
            "name": name,
            "url": href if href.startswith("/") else (f"/list/person/{href}" if href else ""),
        })
    return out


def people_labels(page: str) -> list[str]:
    """Персоны строкой «Автор: Доктор Моро» — так они уезжают в CSV."""
    return [f"{p['role']}: {p['name']}" for p in persons(page)]


def author_of(page: str) -> str | None:
    """
    Главный автор тайтла.

    Роль на сайте бывает «Автор» и «Авторы», а блок персон дублируется в герое
    и во вкладке — поэтому берётся первая уникальная пара.
    """
    return next((p["name"] for p in persons(page) if p["role"].startswith("Автор")), None)


def alt_names(page: str) -> list[str]:
    """Другие названия из модального окна (русское/английское/оригинальное)."""
    start = page.find('id="alt-names-dialog"')
    if start < 0:
        return []
    end = page.find(_ALT_NAMES_END, start)
    block = page[start: end if end > start else len(page)]

    main_title = text_of(_group(_TITLE_H1_RE, page))
    out: list[str] = []
    for _, value in _ALT_NAMES_RE.findall(block):
        name = text_of(value)
        # русское название дублирует заголовок карточки — в список не берём
        if name and name != main_title and name not in out:
            out.append(name)
    return out


def stats(page: str) -> dict[str, int]:
    """Счётчики читателей по техническим ключам: в процессе, отложено, готово."""
    out: dict[str, int] = {}
    for key, _name, value in _STAT_RE.findall(page or ""):
        number = to_int(value)
        if number is not None:
            out[key] = number
    return out


def stats_labels(page: str) -> list[str]:
    """Счётчики подписями сайта: «В процессе: 18» — так они уезжают в CSV."""
    out: list[str] = []
    for _key, name, value in _STAT_RE.findall(page or ""):
        label = f"{text_of(name)}: {value}".strip(": ")
        if label and label not in out:
            out.append(label)
    return out


def manga_id_of(page: str) -> int | None:
    """Внутренний id тайтла со страницы карточки."""
    return next((to_int(value) for regex in _ID_RES if (value := _group(regex, page))), None)


def details_fields(page: str, slug: str, site: str) -> dict[str, Any]:
    """
    Страница карточки -> плоские поля модели.

    Собирает всё, что есть только здесь: описание, статусы, персоны, теги,
    оценки по категориям и статистику читателей.
    """
    fields = info_fields(page)
    collected = tags(page)
    production = _PRODUCTION_RE.search(page)
    sub_ratings = {key: to_float(value) for key, value in _SUB_RATING_RE.findall(page)}

    hero = page.split(_POPULARITY_END, 1)[0]  # огоньки популярности живут только в герое
    title = text_of(_group(_TITLE_H1_RE, page))
    cover_tag = _group(_COVER_TAG_RE, page) or ""

    return {
        "id": manga_id_of(page),
        "slug": slug,
        "url": f"{site}/{slug}" if slug else "",
        "title": title or "",
        "alt_titles": alt_names(page),
        "description": text_of(_group(_DESC_META_RE, page))
        or text_of(_group(_DESC_BLOCK_RE, page)),
        "cover": _group(_IMG_SRC_RE, cover_tag) or _group(_IMG_DATA_SRC_RE, cover_tag),
        "wallpaper": _group(_WALLPAPER_RE, page),
        "genres": collected["genres"],
        "tags": collected["tags"],
        "rating": to_float(_group(_RATING_RE, page)),
        "votes": to_compact_int(_group(_VOTES_RE, page)),
        "story_rating": sub_ratings.get("story"),
        "characters_rating": sub_ratings.get("characters"),
        "world_rating": sub_ratings.get("world"),
        "chapters": to_int(_group(_TAB_LABEL_RE, page)) or len(chapters(page, site)),
        "status": fields.get("Выпуск") or text_of(production.group(2) if production else None),
        "production_status": production.group(1) if production else None,
        "volumes": to_int(fields.get("Томов")),
        "release_frequency": fields.get("Частата выхода"),
        "age_rating": text_of(_group(_LIMITATION_RE, page)),
        "category": text_of(_group(_CATEGORY_RE, page)),
        "year": to_int(_group(_YEAR_RE, page)),
        "popularity": len(_POPULARITY_RE.findall(hero)),
        "author": author_of(page),
        "people": people_labels(page),
        "stats": stats_labels(page),
    }


# ---------- главы ----------

def chapters(page: str, site: str) -> list[dict[str, Any]]:
    """
    Список глав со страницы карточки.

    Список приходит сервером целиком (у тайтла на 300+ глав разметка всё равно
    одна страница), поэтому пагинации нет. Номер главы берётся из URL, а не
    из `data-num`: в атрибуте лежит внутренний счётчик (20-я глава тома 5
    имеет `data-num="200"`).
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for chapter_id, vol_attr, num_attr, block in _CHAPTER_ROW_RE.findall(page or ""):
        link = _CHAPTER_LINK_RE.search(block)
        if not link:
            continue
        href = link.group("href").split("#", 1)[0]
        if href in seen:
            continue
        seen.add(href)

        address = _CHAPTER_URL_RE.search(href)
        date = _CHAPTER_DATE_RE.search(block)
        label = text_of(link.group("text"))

        out.append({
            "id": to_int(chapter_id),
            "vol": to_int(address.group("vol")) if address else to_int(vol_attr),
            "num": to_int(num_attr),
            "number": to_int(address.group("num")) if address else None,
            "title": text_of(_CHAPTER_PREFIX_RE.sub("", label or "")) or label,
            "label": label,
            "uploader": text_of(_group(_CHAPTER_TITLE_ATTR_RE, link.group("attrs"))),
            "url": f"{site}{href}" if href.startswith("/") else href,
            "date": date.group(1) if date else None,
            "date_raw": date.group(2) if date else None,
        })
    return out


# ---------- страницы главы ----------

def reader_pages(page: str) -> list[str]:
    """
    Адреса картинок главы из вызова `rm_h.readerInit`.

    Каждая страчка приходит кортежем `[origin, '', 'путь?подпись', w, h, url]`.
    Обычный адрес — `origin + путь`, но ридер для гостя берёт шестой элемент,
    если он непустой: так отдаются картинки, доступные без подписки.
    """
    start = _READER_MARK_RE.search(page or "")
    if not start:
        return []

    out: list[str] = []
    for match in _READER_PAGE_RE.finditer(page[start.end():]):
        guest, origin, path = match.group("guest"), match.group("origin"), match.group("path")
        if guest:
            out.append(guest)
        else:
            out.append(path if path.startswith("http") else f"{origin.rstrip('/')}/{path.lstrip('/')}")
    return out


__all__ = [
    "tiles", "tile_item", "catalog_page", "catalog_total",
    "details_fields", "info_fields", "tags", "persons", "people_labels", "author_of",
    "alt_names", "stats", "stats_labels", "manga_id_of",
    "chapters", "reader_pages", "text_of",
]
