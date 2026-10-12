"""Разбор HTML InkStory: JSON-LD schema.org + текстовые метки страницы.

У InkStory нет публичного JSON API (/content отдаёт «Only HTML requests are
supported here»), каталог рендерится на сервере, а данные лежат в разметке.

Данные берутся из трёх мест страницы книги:

    1. JSON-LD schema.org (ComicSeries) — название, обложка, описание, жанры
       и `aggregateRating` (рейтинг и число голосов);
    2. блок «Дополнительная информация» — автор, художник, издатели,
       переводчики, журнал и другие названия;
    3. блок характеристик — год, страна, статус, число глав.

Разбор вынесен отдельно от HTTP-клиента, поэтому логику можно покрыть
тестами на фикстурах без сети.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...core.text import page_text, strip_tags, to_compact_int, to_int

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
    """Главы лежат в разметке страницы книги (ссылки на /content/.../chapter/...)."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in re.finditer(r'href="(/content/[^"]*?/chapter/[^"]+)"', page):
        href = match.group(1)
        if href in seen:
            continue
        seen.add(href)
        label_text = strip_tags(page[match.start(): match.start() + 400])
        number = re.search(r"([\d.]+)", label_text)
        out.append({
            "url": site + href,
            "number": float(number.group(1)) if number else None,
            "label": label_text[:80],
        })
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
