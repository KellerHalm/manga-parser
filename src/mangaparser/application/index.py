"""Use-case: офлайн-индекс и поиск по нему (без сети, как локальная база в Mihon).

Индекс наполняется из готовых выгрузок JSON/JSONL — повторно обходить сайт
не нужно, поэтому поиск работает мгновенно и без подключения.
"""

from __future__ import annotations

import json
import time
import zlib
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ..infrastructure.storage import SqliteIndex
from .report import Reporter

#: Поля, из которых собирается поисковый документ (в порядке приоритета).
TITLE_FIELDS = ("title", "name", "secondary_name", "another_name", "eng_name", "alt_title", "label")


def iter_rows(path: Path) -> Iterable[dict[str, Any]]:
    """Строит итератор по записям JSON или JSONL-выгрузки."""
    path = Path(path)
    if not path.exists():
        return
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    yield from (payload if isinstance(payload, list) else [payload])


def stable_uid(row: dict[str, Any]) -> int:
    """
    Числовой id для индекса.

    Если в записи нет целого id (например, InkStory отдаёт только slug),
    используется crc32 от ключа — он стабилен между запусками, в отличие
    встроенного hash(), который рандомизируется интерпретатором.
    """
    raw = row.get("id")
    if isinstance(raw, int) or (isinstance(raw, str) and raw.isdigit()):
        return int(raw)
    key = str(row.get("slug") or row.get("url") or row.get("title") or "")
    return zlib.crc32(key.encode("utf-8")) or 1


def document(row: dict[str, Any]) -> str:
    parts = [str(row[f]) for f in TITLE_FIELDS if row.get(f)]
    return " | ".join(dict.fromkeys(parts))  # dict.fromkeys убирает повторы, порядок сохраняет


def build_index(
    catalogs: Sequence[Path],
    index_path: Path,
    *,
    reporter: Reporter | None = None,
    reset: bool = False,
) -> Path:
    """Наполняет sqlite-индекс из готовых выгрузок (catalog.json / .jsonl)."""
    report = reporter or Reporter()
    index_path = Path(index_path)
    if reset and index_path.exists():
        index_path.unlink()

    index = SqliteIndex(index_path)
    try:
        for catalog in catalogs:
            catalog = Path(catalog)
            docs: dict[int, tuple[str, str]] = {}
            source = catalog.stem.split("_")[0]
            for row in iter_rows(catalog):
                uid = stable_uid(row)
                docs[uid] = (str(row.get("source") or source), document(row))
            if not docs:
                report.warn(f"[!] пусто или нет файла: {catalog}")
                continue
            index.bulk(docs)
            report.info(f"  +{len(docs)} из {catalog.name}")
        report.result(f"Индекс: {index.count()} записей -> {index_path}")
    finally:
        index.close()
    return index_path


def search_index(
    index_path: Path,
    query: str,
    *,
    limit: int = 30,
    reporter: Reporter | None = None,
) -> list[dict[str, Any]]:
    """Мгновенный офлайн-поиск по построенному индексу."""
    report = reporter or Reporter()
    index = SqliteIndex(index_path)
    try:
        started = time.perf_counter()
        hits = index.search(query, limit=limit)
        ms = (time.perf_counter() - started) * 1000
        report.result(f"«{query}»: {len(hits)} за {ms:.0f} мс (в индексе {index.count()})\n")
        for hit in hits:
            report.result(f"  [{hit['id']:>8}] {str(hit['title'])[:60]}")
        return hits
    finally:
        index.close()
