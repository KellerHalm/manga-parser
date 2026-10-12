"""Use-case: поиск и просмотр карточки/глав.

Обёртки над методами порта Source: прикладной слой не знает, JSON это API
или SSR-разметка.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.http import SourceError
from ..core.models import extract_pages
from ..core.ports import Source
from .report import Reporter


def resolve_key(source: Source, key: str, *, reporter: Reporter | None = None) -> str:
    """
    Приводит пользовательский ввод к ключу источника.

    У ReManga ключ — это `dir` вида `<29.04.2026>naruto_`, у MangaLIB slug
    может начинаться с id (`109483--berserk`), вручную их не набрать.
    Поэтому простой slug ищется в каталоге, и уже найденный ключ используется
    как есть. При нескольких совпадениях побеждает самое точное.

    Сравнивается и ключ, и название: slug у источников транслитерирован
    (`berserk_of_gluttony_`), поэтому по одному только ключу русское имя
    тайтла не находилось.
    """
    try:
        source.details(key)
        return key
    except SourceError:
        pass

    needle = key.casefold()
    candidates: list[tuple[int, int, str]] = []
    for item in source.search(key):
        candidate = getattr(item, "key", "") or ""
        if not candidate:
            continue
        title = str(getattr(item, "title", "") or "")
        rank = _match_rank(needle, candidate.casefold(), title.casefold())
        if rank is not None:
            candidates.append((rank, len(title), candidate))

    if not candidates:
        return key
    best = min(candidates)[2]
    if reporter:
        reporter.info(f"[i] ключ уточнён: {key} -> {best}")
    return best


def _match_rank(needle: str, *texts: str) -> int | None:
    """Насколько точно запрос совпал с ключом или названием (меньше — лучше)."""
    best: int | None = None
    for text in texts:
        if not text:
            continue
        if text == needle:
            rank = 0
        elif text.rsplit("-", 1)[-1] == needle or text.endswith(f"--{needle}"):
            rank = 1
        elif needle in text:
            rank = 2
        else:
            continue
        best = rank if best is None else min(best, rank)
    return best


def search(
    source: Source,
    query: str,
    *,
    page: int = 1,
    limit: int = 25,
    reporter: Reporter | None = None,
    **options: Any,
) -> list[Any]:
    """Серверный поиск по названию."""
    report = reporter or Reporter()
    started = time.perf_counter()
    results = source.search(query, page=page, **options)
    elapsed_ms = (time.perf_counter() - started) * 1000

    report.result(f"«{query}» [{source.name}]: {len(results)} за {elapsed_ms:.0f} мс\n")
    for item in results[:limit]:
        report.result(format_row(item))
    return results


def details(source: Source, key: str, *, reporter: Reporter | None = None) -> Any:
    """Карточка тайтла."""
    report = reporter or Reporter()
    item = source.details(key)
    report.result(format_details(item))
    return item


def chapters(source: Source, key: str, *, limit: int = 20, reporter: Reporter | None = None) -> list[Any]:
    """Список глав."""
    report = reporter or Reporter()
    items = source.chapters(key)
    report.result(f"Глав: {len(items)} [{source.name}]\n")
    for chapter in items[:limit]:
        report.result(format_chapter(chapter))
    return items


def catalog_page(
    source: Source,
    *,
    page: int = 1,
    limit: int = 40,
    reporter: Reporter | None = None,
    **options: Any,
) -> list[Any]:
    """Одна страница каталога."""
    report = reporter or Reporter()
    started = time.perf_counter()
    items = source.catalog(page=page, **options)
    elapsed_ms = (time.perf_counter() - started) * 1000
    report.result(f"Страница {page} [{source.name}]: {len(items)} за {elapsed_ms:.0f} мс\n")
    for item in items[:limit]:
        report.result(format_row(item))
    return items


# ---------- форматирование ----------


def _get(item: Any, name: str, default: Any = None) -> Any:
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def format_row(item: Any) -> str:
    """Компактная строка выдачи: id, название, рейтинг, статус."""
    uid = _get(item, "uid") or _get(item, "id") or ""
    title = str(_get(item, "title") or _get(item, "name") or "")[:44]
    rating = _get(item, "rating")
    rating_text = f"{rating}" if rating is not None else "—"
    status = _get(item, "status") or ""
    kind = _get(item, "type") or _get(item, "chapters") or ""
    return f"  [{str(uid):>8}] {title:44} {rating_text:>5}  {str(kind)[:10]:10} {str(status)[:14]}"


def format_details(item: Any) -> str:
    """Карточка: все заполненные поля, без пустых строк."""
    flat = item if isinstance(item, dict) else item.flat()
    title = flat.get("title") or flat.get("name") or flat.get("main_name") or "—"
    lines = [str(title)]
    skip = {"title", "name", "main_name", "description"}
    for key, value in flat.items():
        if key in skip or value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            value = ", ".join(f"{k}: {v}" for k, v in value.items())
        elif isinstance(value, list):
            value = ", ".join(
                str(v.get("name") if isinstance(v, dict) else v) for v in value
            )
        lines.append(f"  {key:16} {str(value)[:120]}")
    description = _get(item, "description")
    if description:
        lines.append(f"  {'description':16} {description[:300]}")
    return "\n".join(lines)


def format_chapter(chapter: Any) -> str:
    if isinstance(chapter, dict):
        number = chapter.get("number")
        title = chapter.get("title") or chapter.get("name") or chapter.get("label") or ""
        url = chapter.get("url") or ""
        cid = chapter.get("id")
    else:
        number = _get(chapter, "number")
        title = _get(chapter, "title")
        url = _get(chapter, "url") or ""
        cid = _get(chapter, "id")
    number_text = f"{number}" if number is not None else "—"
    pages = extract_pages(chapter)
    pages_text = f"стр.{len(pages):<4}" if pages else ""
    return f"  {str(cid or ''):>10}  {number_text:>8}  {str(title)[:40]:40} {pages_text:9} {str(url)[:50]}"
