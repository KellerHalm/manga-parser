"""Use-case: поиск и просмотр карточки/глав.

Обёртки над методами порта Source: прикладной слой не знает, JSON это API
или SSR-разметка.
"""

from __future__ import annotations

import time
from typing import Any

from ..core.http import SourceError
from ..core.models import chapter_field, extract_pages
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


def chapters(
    source: Source,
    key: str,
    *,
    limit: int = 20,
    with_pages: bool = False,
    reporter: Reporter | None = None,
) -> list[Any]:
    """
    Список глав.

    `with_pages` добавляет каждой главе число страниц: у части источников оно
    приходит в самой записи (`page_count`), остальным приходится делать запрос
    на каждую главу, поэтому по умолчанию выключено.
    """
    report = reporter or Reporter()
    items = list(source.chapters(key))
    for chapter in items[:limit] if with_pages else []:
        chapter["pages_count"] = len(source.chapter_pages(key, chapter))

    report.result(f"Глав: {len(items)} [{source.name}]\n")
    for chapter in items[:limit]:
        report.result(format_chapter(chapter))
    return items


def chapter_pages(
    source: Source,
    key: str,
    *,
    chapter: Any = None,
    index: int = 0,
    limit: int = 0,
    reporter: Reporter | None = None,
) -> list[str]:
    """
    Адреса страниц одной главы.

    Глава выбирается по записи из `chapters()`: `chapter` — если он уже есть,
    иначе по `index` в том списке. Список глав при этом не перечитывается,
    если `chapter` передан явно.
    """
    report = reporter or Reporter()
    record = chapter
    if record is None:
        items = source.chapters(key)
        if not items:
            raise SourceError(f"У тайтла нет глав: {key}")
        if not 0 <= index < len(items):
            raise SourceError(f"Главы с номером {index} нет: всего {len(items)}")
        record = items[index]

    pages = source.chapter_pages(key, record)
    report.result(f"Страниц главы {_chapter_label(record, index)} [{source.name}]: {len(pages)}\n")
    for number, url in enumerate(pages[:limit] if limit else pages, 1):
        report.result(f"  {number:>4}  {url}")
    return pages


def _chapter_label(chapter: Any, index: int) -> str:
    """Чем назвать главу в шапке: номером, названием или адресом."""
    return str(
        chapter_field(chapter, "number")
        or chapter_field(chapter, "title")
        or chapter_field(chapter, "url")
        or index
    )


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
    return chapter_field(item, name, default)


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
    """Строка главы: id, номер, название, число страниц и адрес."""
    number = _get(chapter, "number")
    title = _get(chapter, "title") or _get(chapter, "label") or ""
    url = _get(chapter, "url") or ""
    cid = _get(chapter, "id")
    number_text = f"{number}" if number is not None else "—"
    pages_text = _pages_text(chapter)
    return f"  {str(cid or ''):>10}  {number_text:>8}  {str(title)[:40]:40} {pages_text:9} {str(url)[:50]}"


def _pages_text(chapter: Any) -> str:
    """
    Число страниц главы, если оно известно без сетевого запроса.

    Источники отдают его по-разному: BuZuManga кладёт готовый `page_count`,
    ReManga и MangaLIB — сам список страниц в записи главы, `chapters --pages`
    дописывает `pages_count`. Где числа нет, колонка остаётся пустой, а не врёт.
    """
    pages = extract_pages(chapter)
    if pages:
        return f"стр.{len(pages):<4}"
    count = (
        _get(chapter, "page_count")
        or _get(chapter, "pages_count")
        or _get(chapter, "pages")
    )
    return f"стр.{count:<4}" if isinstance(count, int) else ""
