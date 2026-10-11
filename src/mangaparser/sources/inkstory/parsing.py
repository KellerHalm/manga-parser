"""Разбор HTML InkStory: JSON-LD schema.org + текстовые метки страницы.

У InkStory нет публичного JSON API (/content отдаёт «Only HTML requests are
supported here»), каталог рендерится на сервере, а данные лежат в разметке.
Разбор вынесен отдельно от HTTP-клиента, поэтому логику можно покрыть
тестами на фикстурах без сети.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ...core.text import page_text, strip_tags, to_int

#: Границы блока «Метка : значение» в тексте карточки.
_STOP_LABELS = r"(?:Художник|Переводчик|Издатель|Редактор|Другие|Год|Страна|Статус|Жанр|Глав|Метки|\||$)"


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
    """Дополняет карточку полями, которых нет в JSON-LD (автор, год, главы...)."""
    text = page_text(page)

    item.author = after_label(text, "Автор")
    item.artist = after_label(text, "Художник")

    # двоеточие между меткой и значением на разметке может быть, а может и нет
    sep = r"\s*:?\s*"
    if (m := re.search(rf"Год выпуска{sep}(\d{{4}})", text, re.I)):
        item.year = int(m.group(1))
    if (m := re.search(rf"Страна(?: оригинала)?{sep}([А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?)", text)):
        item.source_name = m.group(1)
    if (m := re.search(rf"Статус(?: оригинала)?{sep}([А-ЯЁ][а-яё]+(?:\s+[а-яё]+)?)", text)):
        item.status = m.group(1).strip()
    if (m := re.search(rf"Главы{sep}(\d+)", text, re.I)):
        item.chapters = int(m.group(1))
    if (m := re.search(rf"Просмотры{sep}([\d\s.,Kk]+)", text)):
        item.views = to_int(m.group(1))

    if not item.genres and (m := re.search(rf"Жанры{sep}([^•|·]{{2,200}})", text, re.I)):
        item.genres = [g.strip() for g in re.split(r"[,•|]", m.group(1)) if g.strip()]
    return item
