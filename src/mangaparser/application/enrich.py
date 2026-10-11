"""Use-case: обогащение выгрузки деталями карточек.

Медленная операция — один запрос на книгу, поэтому ограничивается `--limit`
и работает по чекпоинту: обогащённые записи перезаписываются на месте,
краш не теряет уже собранные данные.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.ports import Source
from ..infrastructure.storage import finalize, write_jsonl
from .index import iter_rows
from .report import Reporter


def row_key(row: dict[str, Any]) -> str:
    """Ключ записи внутри выгрузки источника."""
    return str(row.get("slug") or row.get("id") or row.get("url") or row.get("title"))


def enrich_catalog(
    source: Source,
    out: Path,
    *,
    limit: int = 100,
    delay: float | None = None,
    want_csv: bool = True,
    reporter: Reporter | None = None,
) -> dict[str, Any] | None:
    """Дозагружает карточки из чекпоинта и перезаписывает выгрузку."""
    report = reporter or Reporter()
    if not hasattr(source, "enrich"):
        report.warn(f"[!] {source.name}: обогащение не поддерживается")
        return None

    out = Path(out)
    checkpoint = out.with_suffix(".jsonl")
    rows = list(iter_rows(checkpoint))
    if not rows:
        report.warn(f"[!] нет чекпоинта: {checkpoint}")
        return None

    by_key = {row_key(row): row for row in rows}
    keys = list(by_key)[:limit]
    items = [source.item_class.from_flat(by_key[key]) for key in keys]  # type: ignore[attr-defined]

    report.info(f"[i] обогащаю {len(items)} карточек из {checkpoint.name}")
    source.enrich(items, delay=delay)  # type: ignore[attr-defined]
    for key, item in zip(keys, items, strict=False):
        by_key[key] = item.flat()

    write_jsonl(by_key.values(), checkpoint)
    written = finalize(
        by_key.values(),
        out,
        key=getattr(source, "dedupe_key", "id"),
        want_csv=want_csv,
    )
    report.result(
        f"\n{out.name}: {written['rows']} записей, обогащено {len(items)}\n"
        f"  JSON:  {written['json']}"
        + (f"\n  CSV:   {written['csv']}" if want_csv else "")
    )
    return written
