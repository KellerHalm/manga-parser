"""Выгрузки в JSON / JSONL / CSV и дедупликация результата."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ...core.text import key_of


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def write_json(rows: Sequence[dict[str, Any]], path: Path) -> Path:
    ensure_parent(path)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_jsonl(rows: Iterable[dict[str, Any]], path: Path) -> Path:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def write_csv(rows: Sequence[dict[str, Any]], path: Path) -> Path:
    ensure_parent(path)
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return path
    columns: list[str] = []
    for row in rows:
        for name in row:
            if name not in columns:
                columns.append(name)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def finalize(
    rows: Iterable[dict[str, Any]],
    out: Path,
    *,
    key: str,
    want_csv: bool = True,
) -> dict[str, Any]:
    """
    Дедупликация + запись выгрузки. Единая точка сохранения для всех источников.

    Колонки CSV считаются по объединению ключей всех записей, поэтому
    записи с разным набором полей (например, обогащённые) не теряют колонки.
    Записи без поля-ключа не схлопываются между собой — иначе источник без
    такого поля (slug-only каталог) потерял бы всё, кроме одной записи.
    """
    merged: dict[Any, dict[str, Any]] = {}
    for position, row in enumerate(rows):
        identity = key_of(row, key)
        merged.setdefault(position if identity is None else identity, row)

    data = list(merged.values())
    out = Path(out)
    write_json(data, out)
    written = {"json": out, "rows": len(data)}

    csv_path = out.with_suffix(".csv")
    jsonl_path = out.with_suffix(".jsonl")
    if want_csv:
        written["csv"] = write_csv(data, csv_path)
    written["jsonl"] = write_jsonl(data, jsonl_path)
    return written
