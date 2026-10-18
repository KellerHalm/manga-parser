"""Локальное хранилище приложения: избранное, история, прогресс, скачивания.

Одна SQLite-БД рядом с данными приложения. Пишет только она, поэтому
соединение открывается с `check_same_thread=False` и защищено блокировкой:
сетевые вызовы идут в потоках (`page.run_thread`) и касаются БД оттуда.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .paths import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS favorites (
    source     TEXT NOT NULL,
    key        TEXT NOT NULL,
    title      TEXT NOT NULL,
    cover      TEXT,
    rating     REAL,
    status     TEXT,
    added_at   REAL NOT NULL,
    PRIMARY KEY (source, key)
);

CREATE TABLE IF NOT EXISTS history (
    source        TEXT NOT NULL,
    key           TEXT NOT NULL,
    title         TEXT NOT NULL,
    cover         TEXT,
    chapter_key   TEXT,
    chapter_label TEXT,
    page          INTEGER NOT NULL DEFAULT 0,
    pages         INTEGER NOT NULL DEFAULT 0,
    updated_at    REAL NOT NULL,
    PRIMARY KEY (source, key)
);

CREATE TABLE IF NOT EXISTS chapters (
    source      TEXT NOT NULL,
    key         TEXT NOT NULL,
    chapter_key TEXT NOT NULL,
    is_read     INTEGER NOT NULL DEFAULT 0,
    downloaded  INTEGER NOT NULL DEFAULT 0,
    pages       INTEGER NOT NULL DEFAULT 0,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (source, key, chapter_key)
);

CREATE TABLE IF NOT EXISTS downloads (
    source      TEXT NOT NULL,
    key         TEXT NOT NULL,
    chapter_key TEXT NOT NULL,
    title       TEXT NOT NULL,
    label       TEXT,
    pages       INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    PRIMARY KEY (source, key, chapter_key)
);

CREATE TABLE IF NOT EXISTS settings (
    name  TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS history_updated ON history (updated_at DESC);
CREATE INDEX IF NOT EXISTS downloads_created ON downloads (created_at DESC);
"""


def chapter_key(chapter: Any) -> str:
    """
    Устойчивый ключ главы внутри тайтла.

    У источников глава адресуется по-разному, но запись главы всегда несёт
    либо `id`, либо адрес, либо номер. Ключ нужен, чтобы прогресс чтения и
    отметка «прочитано» переживали перезапуск и не зависели от порядка
    глав в списке (он у части сайтов меняется от запроса к запросу).
    """
    if isinstance(chapter, dict):
        for name in ("id", "url", "number"):
            value = chapter.get(name)
            if value not in (None, ""):
                return f"{name}:{value}"
        return "raw:" + str(sorted(chapter.items(), key=lambda kv: kv[0]))
    for name in ("id", "url", "number"):
        value = getattr(chapter, name, None)
        if value not in (None, ""):
            return f"{name}:{value}"
    return "raw:" + str(chapter)


class Library:
    """Хранилище библиотеки: избранное, история чтения и скачанные главы."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self.conn

    # ---------- избранное ----------

    def add_favorite(
        self,
        source: str,
        key: str,
        title: str,
        cover: str | None = None,
        rating: float | None = None,
        status: str | None = None,
    ) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO favorites (source, key, title, cover, rating, status, added_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(source, key) DO UPDATE SET "
                "title=excluded.title, cover=excluded.cover, rating=excluded.rating, "
                "status=excluded.status",
                (source, key, title, cover, rating, status, time.time()),
            )

    def remove_favorite(self, source: str, key: str) -> None:
        with self._write() as conn:
            conn.execute("DELETE FROM favorites WHERE source=? AND key=?", (source, key))

    def is_favorite(self, source: str, key: str) -> bool:
        with self._read() as conn:
            row = conn.execute(
                "SELECT 1 FROM favorites WHERE source=? AND key=?", (source, key)
            ).fetchone()
        return row is not None

    def favorites(self, source: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM favorites"
        params: tuple[Any, ...] = ()
        if source:
            query += " WHERE source=?"
            params = (source,)
        query += " ORDER BY added_at DESC"
        with self._read() as conn:
            return [dict(row) for row in conn.execute(query, params)]

    # ---------- история и прогресс ----------

    def save_progress(
        self,
        source: str,
        key: str,
        title: str,
        chapter: Any,
        label: str | None,
        page: int,
        pages: int,
        cover: str | None = None,
    ) -> None:
        now = time.time()
        ck = chapter_key(chapter)
        with self._write() as conn:
            conn.execute(
                "INSERT INTO history (source, key, title, cover, chapter_key, "
                "chapter_label, page, pages, updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source, key) DO UPDATE SET title=excluded.title, "
                "cover=COALESCE(excluded.cover, history.cover), "
                "chapter_key=excluded.chapter_key, chapter_label=excluded.chapter_label, "
                "page=excluded.page, pages=excluded.pages, updated_at=excluded.updated_at",
                (source, key, title, cover, ck, label, page, pages, now),
            )
            conn.execute(
                "INSERT INTO chapters (source, key, chapter_key, is_read, pages, updated_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(source, key, chapter_key) DO UPDATE SET "
                "pages=MAX(chapters.pages, excluded.pages), updated_at=excluded.updated_at",
                (source, key, ck, int(pages > 0 and page >= pages - 1), pages, now),
            )

    def history(self, limit: int = 60) -> list[dict[str, Any]]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM history ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def progress(self, source: str, key: str) -> dict[str, Any] | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM history WHERE source=? AND key=?", (source, key)
            ).fetchone()
        return dict(row) if row else None

    def chapter_states(self, source: str, key: str) -> dict[str, dict[str, Any]]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM chapters WHERE source=? AND key=?", (source, key)
            ).fetchall()
        return {row["chapter_key"]: dict(row) for row in rows}

    def mark_chapter_read(self, source: str, key: str, chapter: Any, pages: int = 0) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO chapters (source, key, chapter_key, is_read, pages, updated_at) "
                "VALUES (?,?,?,1,?,?) ON CONFLICT(source, key, chapter_key) DO UPDATE SET "
                "is_read=1, pages=MAX(chapters.pages, excluded.pages), updated_at=excluded.updated_at",
                (source, key, chapter_key(chapter), pages, time.time()),
            )

    # ---------- скачивания ----------

    def add_download(
        self, source: str, key: str, chapter: Any, title: str, label: str | None, pages: int
    ) -> None:
        now = time.time()
        ck = chapter_key(chapter)
        with self._write() as conn:
            conn.execute(
                "INSERT INTO downloads (source, key, chapter_key, title, label, pages, created_at) "
                "VALUES (?,?,?,?,?,?,?) ON CONFLICT(source, key, chapter_key) DO UPDATE SET "
                "pages=excluded.pages, created_at=excluded.created_at",
                (source, key, ck, title, label, pages, now),
            )
            conn.execute(
                "INSERT INTO chapters (source, key, chapter_key, downloaded, pages, updated_at) "
                "VALUES (?,?,?,1,?,?) ON CONFLICT(source, key, chapter_key) DO UPDATE SET "
                "downloaded=1, updated_at=excluded.updated_at",
                (source, key, ck, pages, now),
            )

    def remove_download(self, source: str, key: str, chapter: Any) -> None:
        ck = chapter_key(chapter)
        with self._write() as conn:
            conn.execute(
                "DELETE FROM downloads WHERE source=? AND key=? AND chapter_key=?",
                (source, key, ck),
            )
            conn.execute(
                "UPDATE chapters SET downloaded=0 WHERE source=? AND key=? AND chapter_key=?",
                (source, key, ck),
            )

    def downloads(self) -> list[dict[str, Any]]:
        with self._read() as conn:
            rows = conn.execute("SELECT * FROM downloads ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def is_downloaded(self, source: str, key: str, chapter: Any) -> bool:
        with self._read() as conn:
            row = conn.execute(
                "SELECT 1 FROM downloads WHERE source=? AND key=? AND chapter_key=?",
                (source, key, chapter_key(chapter)),
            ).fetchone()
        return row is not None

    def delete_title_data(self, source: str, key: str) -> None:
        """Убрать тайтл из библиотеки вместе с историей и отметками."""
        with self._write() as conn:
            for table in ("favorites", "history", "chapters", "downloads"):
                conn.execute(f"DELETE FROM {table} WHERE source=? AND key=?", (source, key))

    # ---------- настройки ----------

    def get_setting(self, name: str, default: str | None = None) -> str | None:
        with self._read() as conn:
            row = conn.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, name: str, value: str | None) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO settings (name, value) VALUES (?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value",
                (name, value),
            )

    def clear(self, tables: Sequence[str]) -> None:
        with self._write() as conn:
            for table in tables:
                conn.execute(f"DELETE FROM {table}")

    def close(self) -> None:
        with self._lock:
            self.conn.close()