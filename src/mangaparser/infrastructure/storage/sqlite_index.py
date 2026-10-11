"""Офлайн-индекс на SQLite FTS5 — поиск без сети, как локальная база в Mihon.

Индекс — реализация порта `core.ports.OfflineIndex`, работает с любым
`Item` (нужны id и alt-названия), поэтому не привязан к источнику.

Схема: обычная таблица `titles` (id — первичный ключ) + FTS5-таблица
`titles_fts` в режиме external content. FTS5 не умеет UPSERT и не имеет
индекса по UNINDEXED-колонкам, поэтому «удалить и вставить» по id на такой
таблице сканировало бы её целиком на каждую строку (квадратичное поведение).
Данные лежат в `titles`, а полнотекстовый индекс перестраивается одной
командой `rebuild` — это линейно.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Any

TOKENIZER = "unicode61 remove_diacritics 2"


class SqliteIndex:
    """
    Офлайн-поиск по sqlite: мгновенный отклик и работа без сети.

    AND-семантика по префиксам: «sol level» найдёт «Solo Leveling»,
    «бер» найдёт «Берсерк». Порядок — по релевантности FTS5 (bm25).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS titles ("
            "id INTEGER PRIMARY KEY, source TEXT NOT NULL DEFAULT '', doc TEXT NOT NULL)"
        )
        self.conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS titles_fts USING fts5("
            f"doc, content='titles', content_rowid='id', tokenize='{TOKENIZER}')"
        )
        self.conn.commit()

    # ---------- запись ----------

    @staticmethod
    def _doc(item: Any) -> str:
        parts = [getattr(item, "title", "")]
        alt = getattr(item, "alt_titles", None) or getattr(item, "genres", None) or []
        parts.extend(alt.values() if isinstance(alt, dict) else alt)
        return " | ".join(str(p) for p in parts if p)

    def add(self, items: Sequence[Any]) -> None:
        """Добавляет объекты Item: {uid: (source, document)} -> перестроение индекса."""
        docs = {
            int(getattr(item, "uid", 0) or getattr(item, "id", 0)):
                (str(getattr(item, "source", "")), self._doc(item))
            for item in items
        }
        self.bulk(docs)

    def bulk(self, docs: dict[int, tuple[str, str]]) -> None:
        """
        Массовая загрузка: docs = {uid: (source, document)}.

        Принимает готовые документы (например, собранные из выгрузки JSON),
        где нет объектов Item.
        """
        if not docs:
            return
        self.conn.executemany(
            "INSERT OR REPLACE INTO titles(id, source, doc) VALUES (?,?,?)",
            [(uid, source, doc) for uid, (source, doc) in docs.items()],
        )
        self.rebuild()
        self.conn.commit()

    def rebuild(self) -> None:
        """Перестраивает полнотекстовый индекс по таблице titles."""
        self.conn.execute("INSERT INTO titles_fts(titles_fts) VALUES ('rebuild')")

    def clear(self) -> None:
        self.conn.execute("DELETE FROM titles")
        self.rebuild()
        self.conn.commit()

    # ---------- чтение ----------

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0]

    def sources(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT source, COUNT(*) FROM titles GROUP BY source").fetchall()
        return {source or "?": total for source, total in rows}

    def search(self, query: str, limit: int = 60) -> list[dict[str, Any]]:
        tokens = [t for t in re.split(r"\s+", query.strip()) if t]
        if not tokens:
            return []
        needle = tokens[0].casefold()
        safe = " ".join(f'"{t}"*' for t in tokens)

        try:
            ranked = self.conn.execute(
                "SELECT t.id, t.doc FROM titles_fts "
                "JOIN titles t ON t.id = titles_fts.rowid "
                "WHERE titles_fts MATCH ? ORDER BY bm25(titles_fts) LIMIT ?",
                (safe, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # FTS5 может не знать синтаксис запроса — деградация до LIKE
            ranked = self.conn.execute(
                "SELECT id, doc FROM titles WHERE doc LIKE ? LIMIT ?",
                (f"%{query.strip()}%", limit),
            ).fetchall()

        results: list[dict[str, Any]] = []
        for rank, (uid, doc) in enumerate(ranked):
            parts = [p for p in (doc or "").split(" | ") if p]
            if not parts:
                continue
            # приоритет: точное начало названия -> основное -> альтернативные
            title = next((p for p in parts if p.casefold().startswith(needle)), parts[0])
            results.append({"id": uid, "title": title, "rank": rank})
        return results

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> SqliteIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
