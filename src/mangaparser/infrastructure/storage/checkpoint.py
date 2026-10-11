"""JSONL-чекпоинт: прогресс обхода пишется по мере поступления.

Нужен, чтобы обход на десятки тысяч записей переживал краш, Ctrl+C и 429 —
при повторном запуске уже собранное не парсится заново.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any


class JsonlCheckpoint:
    """Дописываемый JSONL-файл прогресса + чтение накопленного."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fh = None

    def load(self) -> list[dict[str, Any]]:
        """Читает накопленные записи, пропуская повреждённые строки."""
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def append(self, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        if self._fh is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
        for row in rows:
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def flush(self) -> None:
        if self._fh is not None:
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> JsonlCheckpoint:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
