"""Use-case: склейка выгрузок нескольких источников в один файл.

Ключ дедупликации — `source:uid`: тайтлы с одинаковым номером у разных
сайтов не должны склеиваться в одну запись.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..infrastructure.storage import finalize
from .report import Reporter


def load_catalog(path: Path) -> list[dict[str, Any]]:
    """Читает JSON-выгрузку каталога (список записей)."""
    path = Path(path)
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, list) else [payload]


def merge_catalogs(
    catalogs: Sequence[Path],
    out: Path,
    *,
    want_csv: bool = True,
    reporter: Reporter | None = None,
) -> dict[str, Any]:
    """Склеивает несколько выгрузок в один JSON/CSV."""
    report = reporter or Reporter()
    rows: list[dict[str, Any]] = []
    for catalog in catalogs:
        path = Path(catalog)
        if not path.exists():
            report.warn(f"[!] нет файла: {path}")
            continue
        source = path.stem.split("_")[0]
        loaded = load_catalog(path)
        for row in loaded:
            row = dict(row)
            row.setdefault("source", source)
            row["uid"] = f"{source}:{row.get('id') or row.get('slug') or row.get('url')}"
            rows.append(row)

    written = finalize(rows, out, key="uid", want_csv=want_csv)
    report.result(
        f"{out.name}: {written['rows']} записей\n"
        f"  JSON:  {written['json']}"
        + (f"\n  CSV:   {written['csv']}" if want_csv else "")
    )
    return written
