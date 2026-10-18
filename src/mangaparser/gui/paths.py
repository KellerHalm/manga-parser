"""Пути приложения: где лежат БД, кэш и скачанные главы.

Каталог выбирается по платформе, потому что на Android/iOS текущий
рабочий каталог недоступен для записи, а на десктопе писать в него не нужно.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_DIR_NAME = "manga-parser"


def app_dir() -> Path:
    """Каталог данных приложения, создаётся при первом обращении."""
    if sys.platform in ("android", "ios"):
        base = Path(os.environ.get("HOME") or Path.home())
        root = base / "Documents" / APP_DIR_NAME
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        root = base / APP_DIR_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        root = base / APP_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    return app_dir() / "library.sqlite3"


def covers_dir() -> Path:
    """Кэш обложек и страниц глав, скачанных для офлайн-чтения."""
    path = app_dir() / "covers"
    path.mkdir(parents=True, exist_ok=True)
    return path


def chapters_dir() -> Path:
    path = app_dir() / "chapters"
    path.mkdir(parents=True, exist_ok=True)
    return path