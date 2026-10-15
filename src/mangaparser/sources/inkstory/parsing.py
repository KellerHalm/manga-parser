"""Разбор HTML InkStory: JSON-LD schema.org + текстовые метки страницы.

У InkStory нет публичного JSON API (/content отдаёт «Only HTML requests are
supported here»), каталог рендерится на сервере, а данные лежат в разметке.

Данные берутся из трёх мест страницы книги:

    1. JSON-LD schema.org (ComicSeries) — название, обложка, описание, жанры
       и `aggregateRating` (рейтинг и число голосов);
    2. блок «Дополнительная информация» — автор, художник, издатели,
       переводчики, журнал и другие названия;
    3. блок характеристик — год, страна, статус, число глав.

Списки глав и страниц глав разбираются отдельно: главы лежат на вкладке
`/content/<slug>/chapters`, а страницы — в SSR-payload ридера.

Разбор вынесен отдельно от HTTP-клиента, чтобы логику можно покрыть
тестами на фикстурах без сети.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...core.text import page_text, strip_tags, to_compact_int, to_float, to_int

#: Границы блока «Метка : значение» в тексте карточки.
_STOP_LABELS = r"(?:Художник|Переводчик|Издатель|Редактор|Другие|Год|Страна|Статус|Жанр|Глав|Метки|\||$)"

#: Блок «Дополнительная информация»:
#: <span class="w-36 …">Метка<!-- -->:</span><div>значение</div>
_INFO_FIELD_RE = re.compile(
    r'<span class="w-36[^"]*">\s*(?P<label>[^<]+?)\s*(?:<!--\s*-->)?\s*:\s*</span>\s*'
    r"<div[^>]*>(?P<value>.*?)</div>\s*</div>",
    re.S,
)

#: Блок характеристик под обложкой:
#: <p class="text-sm font-semibold">Метка</p><p>значение</p>
_STAT_FIELD_RE = re.compile(
    r'<p class="text-sm font-semibold">\s*(?P<label>[^<]+?)\s*</p>\s*'
    r"<(?:p|a)[^>]*>(?P<value>.*?)</(?:p|a)>",
    re.S,
)

#: Глава на вкладке «Главы»: ссылка-обложка несёт читаемое название главы,
#: адрес главы — uuid: /content/<slug>/<uuid>
_CHAPTER_LINK_RE = re.compile(
    r'<a aria-label="(?P<label>[^"]*)"\s+href="(?P<href>/content/[^"/]+/[0-9a-f-]{36})"',
    re.I,
)
_LABEL_NUMBER_RE = re.compile(r"Глава\s+(?P<number>[\d.]+)")
_LABEL_TITLE_RE = re.compile(r"^Глава\s+[\d.]+\s*:?\s*")
#: Том главы — моноширинный бейдж рядом с названием: <span …>108</span>
_VOLUME_RE = re.compile(r'font-mono[^"]*"[^>]*>\s*(\d+)\s*</span>', re.S)
_DATE_RE = re.compile(r"(\d{2}\.\d{2}\.\d{4})")

#: Ридер отдаётся сервером: в payload есть объект открытой главы со списком
#: страниц. `initialChapter` идёт последним в payload страницы, поэтому
#: разбор начинается с него, а не с начала документа.
_INITIAL_CHAPTER_RE = re.compile(r"initialChapter:\$R\[\d+\]=\{")
_CHAPTER_ID_RE = re.compile(r'id:"(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"')
_PAGE_IMAGE_RE = re.compile(r'image:"(https://static\.inuko\.me/chapters/[^"]+)"')

#: Сколько разметки после ссылки главы просматривается в поисках тома и даты.
_CARD_TAIL = 1200


def jsonld_nodes(page: str) -> list[dict[str, Any]]:
    """Все JSON-LD объекты со страницы (@graph разворачивается)."""
    out: list[dict[str, Any]] = []
    for match in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S
    ):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            out.extend(data.get("@graph", []) if "@graph" in data else [data])
        elif isinstance(data, list):
            out.extend(data)
    return out


def collection_items(page: str) -> list[dict[str, Any]]:
    """Список ComicSeries со страницы каталога через JSON-LD ItemList."""
    for node in jsonld_nodes(page):
        if node.get("@type") == "CollectionPage":
            main = node.get("mainEntity") or {}
            items = main.get("itemListElement") or []
            return [
                it.get("item", {})
                for it in items
                if isinstance(it, dict) and isinstance(it.get("item"), dict)
            ]
    return []


def series_node(page: str) -> dict[str, Any]:
    """Объект ComicSeries со страницы книги."""
    for node in jsonld_nodes(page):
        if node.get("@type") == "ComicSeries":
            return node
    return {}


def page_fields(page: str) -> dict[str, str]:
    """
    Метки и их значения со страницы книги.

    Разметка у карточки регулярная, поэтому значения берутся прямо из тегов,
    а не из видимого текста: так «Издатель» не съедает соседнюю строку, а
    «Другие названия» — список до конца. Оба блока сливаются в один словарь;
    при совпадении меток побеждает первая (информационный блок важнее).
    """
    fields: dict[str, str] = {}
    for regex in (_INFO_FIELD_RE, _STAT_FIELD_RE):
        for match in regex.finditer(page):
            value = strip_tags(match.group("value"))
            if value:
                fields.setdefault(match.group("label").strip(), value)
    return fields


def split_list(value: str | None, separator: str = r"\s*,\s*") -> list[str]:
    """Список из значения метки: «A, B , C» -> ['A', 'B', 'C']."""
    return [part.strip() for part in re.split(separator, value or "") if part.strip()]


def after_label(text: str, label_: str, limit: int = 60) -> str | None:
    """Значение, стоящее после «Метка :» в тексте страницы."""
    match = re.search(
        rf"{label_}\s*:?\s*(.{{1,{limit}}}?)(?=\s*{_STOP_LABELS})",
        text,
        re.I,
    )
    if not match:
        return None
    return match.group(1).strip(" ·|,-—") or None


def chapters_from_page(page: str, site: str) -> list[dict[str, Any]]:
    """
    Главы тайтла — лежат в разметке вкладки «Главы».

    Адрес главы — `/content/<slug>/<uuid>`, без номера в пути: идентификатор
    главы на сайте uuid, а читаемый номер с названием лежит в `aria-label`
    ссылки. Том и дата главы идут следом в тексте карточки главы.

    Сервер отдаёт только первую виртуальную страницу списка (41 глава при
    1217 у One Piece): остальное догружает клиент через `/api`, а он закрыт
    в `robots.txt`, поэтому `?page=` и не помогает — подгрузки нет и быть
    не может без обхода запрета.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _CHAPTER_LINK_RE.finditer(page or ""):
        href, label = match.group("href"), strip_tags(match.group("label"))
        if href in seen:
            continue
        seen.add(href)

        tail = page[match.end(): match.end() + _CARD_TAIL]
        volume = _VOLUME_RE.search(tail)
        date = _DATE_RE.search(tail)
        number = _LABEL_NUMBER_RE.match(label)
        out.append({
            "id": href.rsplit("/", 1)[-1],
            "number": to_float(number.group("number")) if number else None,
            "title": _LABEL_TITLE_RE.sub("", label).strip() or None,
            "label": label or None,
            "volume": to_int(volume.group(1)) if volume else None,
            "date": date.group(1) if date else None,
            "url": f"{site}{href}",
        })
    return out


def reader_pages(page: str) -> list[str]:
    """
    Адреса картинок главы из SSR-payload ридера.

    Разметка ридера отдаётся сервером целиком, и в ней лежит объект
    `initialChapter` со списком `pages` — у каждой страницы свой `image`.
    Адрес главы в адресе картинки, поэтому берутся только страницы этой главы:
    у соседних глав в payload свои префиксы, и без фильтра они бы попали в выдачу.

    Порядок страниц совпадает с порядком `index` в payload — он идёт по возрастанию.
    """
    start = _INITIAL_CHAPTER_RE.search(page or "")
    if not start:
        return []
    chapter_id = _CHAPTER_ID_RE.search(page, start.start())
    if not chapter_id:
        return []
    return _dedupe(_PAGE_IMAGE_RE.findall(page, chapter_id.end()))


def _dedupe(urls: list[str]) -> list[str]:
    """Уникальные адреса с сохранением порядка: превью повторяет картинку."""
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def total_pages(page: str, fallback: int) -> int:
    """Число страниц каталога из пагинации в разметке."""
    pages = {int(p) for p in re.findall(r"page=(\d+)", page) if p.isdigit()}
    return max(pages) if pages else fallback


def apply_details_fields(item: Any, page: str) -> Any:
    """
    Дополняет карточку полями, которых нет в JSON-LD.

    Основной путь — разметка меток: значения берутся прямо из тегов, поэтому
    списки издателей и переводчиков не обрезаются. Если блок не найден,
    остаётся разбор видимого текста: он менее точен, но переживает смену
    вёрстки.
    """
    fields = page_fields(page)
    text = page_text(page)

    def label(label_: str) -> str | None:
        return fields.get(label_) or after_label(text, label_)

    def labeled(label_: str, pattern: str) -> str | None:
        """
        Значение метки из разметки, иначе по регулярке видимого текста.

        Регистр учитывается: метки приходят в фиксированном виде, а при
        нечувствительности к нему класс `[а-яё]` захватывал бы следующую
        строку («Завершён Главы»).
        """
        if value := fields.get(label_):
            return value
        match = re.search(pattern, text)
        return match.group(1).strip() if match else after_label(text, label_)

    def counter(label_: str) -> int | None:
        """Счётчик вида «73M» или «826k»: сайт округляет, значение нужно развернуть."""
        if value := fields.get(label_):
            return to_compact_int(value)
        match = re.search(rf"{label_}\s*:?\s*([\d\s.,]+[kKmMbB]?)", text)
        return to_compact_int(match.group(1)) if match else None

    item.author = label("Автор")
    item.artist = label("Художник")
    item.journals = split_list(fields.get("Журнал"))
    item.publishers = split_list(fields.get("Издатель"))
    item.translators = split_list(fields.get("Переводчик"))
    item.alt_titles = split_list(fields.get("Другие названия"), separator=r"\s*/\s*")

    if value := labeled("Год выпуска", r"Год выпуска\s*:?\s*(\d{4})"):
        item.year = to_int(value)
    if value := labeled("Страна оригинала", r"Страна(?: оригинала)?\s*:?\s*([А-ЯЁ][а-яё-]+)"):
        item.source_name = value
    if value := labeled("Статус оригинала", r"Статус(?: оригинала)?\s*:?\s*([А-ЯЁ][а-яё]+(?:\s+[а-яё]+)?)"):
        item.status = value
    if value := labeled("Главы", r"Главы\s*:?\s*(\d+)"):
        item.chapters = to_int(value)
    item.views = counter("Просмотры")
    item.likes = counter("Лайки")

    if not item.genres:
        item.genres = split_list(fields.get("Жанры"))
    return item
