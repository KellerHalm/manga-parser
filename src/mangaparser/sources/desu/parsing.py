"""Разбор desu.uno: `sitemap.php` и разметка карточки тайтла.

Сайт на XenForo, публичного JSON API нет — всё нужное лежит в обычной
HTML-разметке и микроразметке schema.org. Данные берутся из двух мест:

    1. `/sitemap.php` — весь каталог: `id`, `slug`, название из slug, `lastmod`.
       Пагинация каталога `/manga/?page=N` запрещена в `robots.txt`, поэтому
       обход всего каталога идёт по sitemap (один сетевой запрос);
    2. карточка `/manga/<slug>.<id>/` — описание, жанры, авторы, переводчики,
       рейтинг, просмотры, статус и полный список глав (одним списком, до
       ~1200 ссылок, пагинации у глав нет).

Карточка построена регулярно, поэтому значения берутся прямо из тегов:

    <div class="b-entry-info">
      <div class="line-container"><div class="line">
        <div class="key">Статус:</div>
        <div class="value"><span class="b-anime_status_tag ongoing">выходит</span>
                           с 03 дек 2018 г.</div>
      </div></div>

Значение метки вырезается по следующей `class="key"`, а не по `</div>`:
внутри значения бывают вложенные `div` (Читать/Просмотров), и по `</div>`
строка обрезалась бы на середине.

Разбор вынесен отдельно от HTTP-клиента, чтобы покрыть его тестами на
фикстурах без сети.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...core.text import strip_tags, to_float, to_int

#: Адрес карточки: /manga/<slug>.<id>/ — id идёт последним, поэтому в slug
#: может быть точка (встречаются slug вроде `fate.type-redline`).
MANGA_URL_RE = re.compile(r"^https?://desu\.uno/manga/(?P<slug>.+)\.(?P<id>\d+)/?$", re.I)

#: Глава: /manga/<slug>.<id>/vol108/ch1196/rus — номер главы дробный (ch48.5).
CHAPTER_PATH_RE = re.compile(r"/vol(?P<volume>[\d.]*)/ch(?P<number>[\d.]+)/")

_LOC_RE = re.compile(r"<loc>(.*?)</loc>", re.S)
_URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>", re.S)
_LASTMOD_RE = re.compile(r"<lastmod>(.*?)</lastmod>", re.S)

_META_RE = re.compile(r"<meta\b[^>]*>", re.S)
_DIV_RE = re.compile(r"<div\b[^>]*>|</div>", re.S)
_IMG_RE = re.compile(r"<img\b[^>]*>", re.S)
_SRC_RE = re.compile(r'src="([^"]+)"', re.S)
_CONTENT_RE = re.compile(r'content="([^"]*)"', re.S)
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)

#: Метки карточки: «Тип:», «Статус:», «Жанры:», «Переводчики:» и т.д.
_KEY_RE = re.compile(r"<div class=\"key\">\s*(?P<label>[^<]+?)\s*:?\s*</div>")
_STATUS_TAG_RE = re.compile(r'<span class="b-anime_status_tag [^"]*">(.*?)</span>', re.S)
_SCORE_RE = re.compile(r'data-score="([\d.]+)"')

_NAME_RE = re.compile(r'<span class="name"[^>]*>(.*?)</span>', re.S)
_RUS_NAME_RE = re.compile(r'<span class="rus-name"[^>]*>(.*?)</span>', re.S)

#: Список глав карточки: <ul class="chlist">…</ul>
_CHLIST_RE = re.compile(r'<ul class="chlist"[^>]*>(.*?)</ul>', re.S)
_CHAPTER_ID_RE = re.compile(r'data-chapters_id="(\d+)"')
_DATE_RE = re.compile(r'<span class="date">([^<]*)</span>')
_H4_RE = re.compile(r"<h4>(.*?)</h4>", re.S)
_CHAPTER_HREF_RE = re.compile(r'<h4><a href="([^"]+)"')

_YEAR_RE = re.compile(r"(\d{4})")


# ---------- sitemap ----------


def entry(url: str, lastmod: str | None = None) -> dict[str, Any] | None:
    """Адрес карточки -> запись каталога (None, если это не карточка манги)."""
    raw = (url or "").strip()
    match = MANGA_URL_RE.match(raw)
    if not match:
        return None
    slug, ident = match.group("slug"), int(match.group("id"))
    return {
        "id": ident,
        "url": raw.rstrip("/") + "/",
        "slug": slug,
        "title": title_from_slug(slug),
        "updated_at": lastmod,
    }


def entries(page: str) -> list[dict[str, Any]]:
    """Записи каталога со страницы sitemap (корень сайта в них не попадает)."""
    out: list[dict[str, Any]] = []
    for block in _URL_BLOCK_RE.findall(page or ""):
        loc = _LOC_RE.search(block)
        if not loc:
            continue
        lastmod = _LASTMOD_RE.search(block)
        row = entry(loc.group(1), lastmod.group(1) if lastmod else None)
        if row:
            out.append(row)
    return out


def title_from_slug(slug: str) -> str:
    """
    `one-piece` -> `One Piece`.

    Обратной транслитерации не существует, поэтому до обогащения название
    остаётся латиницей — это ровно то, что публикует sitemap.
    """
    words = [w for w in re.split(r"[-_]+", slug or "") if w]
    return " ".join(w if w.isupper() else w.capitalize() for w in words)


# ---------- карточка ----------


def inner(page: str, marker: str) -> str:
    """
    Внутренность блока `<div>` по маркеру внутри его открывающего тега.

    Вложенность считается по `<div>`/`</div>`: значение метки «Читать» само
    лежит во вложенном `<div class="value">`, и обрезка по первому `</div>`
    вернула бы половину строки.
    """
    start = page.find(marker)
    if start < 0:
        return ""
    body = page.find(">", start) + 1
    depth = 1
    for match in _DIV_RE.finditer(page[body:]):
        depth += 1 if match.group(0).startswith("<div") else -1
        if not depth:
            return page[body: body + match.start()]
    return ""


def info_regions(page: str) -> dict[str, str]:
    """Метки карточки («Статус», «Жанры») -> сырой HTML их значения."""
    block = inner(page, '<div class="b-entry-info"')
    if not block:
        return {}
    keys = list(_KEY_RE.finditer(block))
    regions: dict[str, str] = {}
    for index, key in enumerate(keys):
        end = keys[index + 1].start() if index + 1 < len(keys) else len(block)
        regions[key.group("label")] = block[key.end(): end]
    return regions


def info_value(page: str, label: str) -> str | None:
    """Текст значения метки: «Просмотров» -> «229035»."""
    value = strip_tags(info_regions(page).get(label, ""))
    return value or None


def info_list(page: str, label: str) -> list[str]:
    """
    Список из значения метки: «Жанры» -> ['Драма', 'Экшен'].

    Большинство меток — это `<ul>` со списком, но «Другие названия» приходят
    одним текстом, склеенным запятой: там список собирается из строки.
    Многоточие в разметке — это кнопка раскрытия длинных списков, а не часть
    значения, поэтому оно убирается до разбора.
    """
    region = info_regions(page).get(label, "").replace("…", "")
    items = [strip_tags(item) for item in _LI_RE.findall(region)]
    if not items:
        items = split_list(strip_tags(region))
    return [item for item in items if item]


def split_list(value: str, separator: str = r"\s*,\s*") -> list[str]:
    """Текст со списком -> список: «A, B, C» -> ['A', 'B', 'C']."""
    return [part.strip() for part in re.split(separator, value or "") if part.strip()]


def meta(page: str, prop: str) -> str | None:
    """Значение `<meta>` микроразметки — атрибуты в разметке идут в любом порядке."""
    for tag in _META_RE.findall(page or ""):
        if (f'itemprop="{prop}"' in tag or f'property="{prop}"' in tag) and (
            value := _CONTENT_RE.search(tag)
        ):
            return value.group(1) or None
    return None


def names(page: str) -> tuple[str | None, str | None]:
    """
    (русское название, оригинальное название) из заголовка карточки.

    Заголовок — `<h1><span class="name">One Piece</span> / <span class="rus-name">
    Ван Пис</span></h1>`; если вёрстка сменится, остаются микротеги
    `headline` (русское) и `alternativeHeadline` (оригинальное).
    """
    russian = strip_tags(russian_match.group(1)) if (russian_match := _RUS_NAME_RE.search(page)) else None
    original = strip_tags(original_match.group(1)) if (original_match := _NAME_RE.search(page)) else None
    return russian or meta(page, "headline"), original or meta(page, "alternativeHeadline")


def cover(page: str) -> str | None:
    """Обложка из блока постера (на og:image падает картинка меньшего размера)."""
    poster = inner(page, '<div class="c-poster')
    for tag in _IMG_RE.findall(poster):
        if 'itemprop="image"' in tag and (src := _SRC_RE.search(tag)):
            return src.group(1)
    return meta(page, "og:image")


def description(page: str) -> str | None:
    """Описание тайтла (у части тайтлов оно пустое)."""
    return strip_tags(inner(page, 'itemprop="description"')) or None


def rating(page: str) -> float | None:
    """Рейтинг 1..10. `data-score="0"` у новых тайтлов — это «нет оценки»."""
    score = _SCORE_RE.search(page)
    value = to_float(score.group(1)) if score else to_float(meta(page, "ratingValue"))
    return value or None


def votes(page: str) -> int | None:
    """Число голосов: у новых тайтлов рейтинга ещё нет, вместе с ним и голосов."""
    return to_int(meta(page, "ratingCount"))


def status(page: str, label: str) -> str | None:
    """
    Статус из тега метки: «выходит», «издано», «продолжается», «завершён».

    Тег ищется внутри самой метки, а не по странице: у карточки их два
    (статус оригинала и статус перевода), и они различаются только меткой.
    """
    region = info_regions(page).get(label, "")
    match = _STATUS_TAG_RE.search(region)
    return strip_tags(match.group(1)) if match else None


def year(value: str | None) -> int | None:
    """Год из строки статуса: «с 21 сен 1999 г. по 10 ноя 2014 г.» -> 1999."""
    match = _YEAR_RE.search(value or "")
    return int(match.group(1)) if match else None


def chapters(page: str, site: str) -> list[dict[str, Any]]:
    """
    Главы тайтла — весь список лежит на карточке, одним списком.

    Страницы глав (`/manga/*/vol*/ch*/`) в `robots.txt` запрещены, поэтому
    список берётся отсюда: том, номер, название и дата есть в разметке.
    """
    block = _CHLIST_RE.search(page or "")
    if not block:
        return []

    out: list[dict[str, Any]] = []
    for item in _LI_RE.findall(block.group(1)):
        href = _CHAPTER_HREF_RE.search(item)
        if not href:
            continue
        path = CHAPTER_PATH_RE.search(href.group(1))
        label = strip_tags(h4.group(1)) if (h4 := _H4_RE.search(item)) else ""
        _, _, name = label.partition(" - ")
        ident = _CHAPTER_ID_RE.search(item)
        date = _DATE_RE.search(item)
        out.append({
            "id": int(ident.group(1)) if ident else None,
            "number": to_float(path.group("number")) if path else None,
            "volume": to_int(path.group("volume")) if path and path.group("volume") else None,
            "title": name.strip() or None,
            "label": label or None,
            "date": strip_tags(date.group(1)) if date else None,
            "url": _absolute(href.group(1), site),
        })
    return out


def _absolute(url: str, site: str) -> str:
    """Адрес главы приходит относительным (`manga/one-piece.12/vol1/ch0/rus`)."""
    if url.startswith("http"):
        return url
    return f"{site}/{url.lstrip('./')}"


#: Первая глава тома — единственная страница главы, разрешённая robots.txt:
#: `Disallow: /manga/*/vol*/ch*/` со смягчающим `Allow: /manga/*/vol*/ch1/rus`.
#: Адрес главы всегда заканчивается языковым хвостом, а не просто `ch1`.
ROBOTS_FREE_CHAPTER_RE = re.compile(r"/vol(?P<volume>[\d.]*)/ch1/rus/?$")


def is_robots_free_chapter(url: str | None) -> bool:
    """Разрешена ли страница главы правилами robots.txt сайта."""
    return bool(url and ROBOTS_FREE_CHAPTER_RE.search(url))


#: Конфигурация ридера: window.MangaReader = {…}
_READER_STATE_RE = re.compile(r"window\.MangaReader\s*=\s*(\{.*?\});", re.S)


def reader_state(page: str) -> dict[str, Any]:
    """Конфигурация ридера из страницы главы (JSON-объект `window.MangaReader`)."""
    match = _READER_STATE_RE.search(page or "")
    if not match:
        return {}
    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def chapter_page_urls(chapter: dict[str, Any]) -> list[str]:
    """Адреса картинок главы из ответа `/api/manga/<id>/chapters/<id>`."""
    pages = chapter.get("pages")
    out: list[str] = []
    for page in pages or []:
        url = page.get("url") if isinstance(page, dict) else page
        if url:
            out.append(str(url))
    return out


def genres(page: str) -> tuple[list[str], list[str]]:
    """
    (жанры, рубрики сайта).

    В одном списке лежат и жанры сайта, и его собственные теги — те помечены
    префиксом «#», поэтому они уходят в отдельное поле (как `categories`
    у ReManga), а не мешаются жанрам.
    """
    out: list[str] = []
    tags: list[str] = []
    for name in info_list(page, "Жанры"):
        (tags if name.startswith("#") else out).append(name.lstrip("# "))
    return out, tags