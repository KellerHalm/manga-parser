#!/usr/bin/env python3
"""
Точка входа проекта: запускает любой парсер.

    python main.py                                    список источников и команд
    python main.py sources                            только источники
    python main.py mangalib  search "наруто"
    python main.py remanga   dump --csv --max-pages 5
    python main.py buzumanga catalog --page 3
    python main.py comx      search "schenok"
    python main.py inkstory  catalog --page 3
    python main.py all       search "берсерк"
    python main.py index     --catalog data/remanga_catalog.json
    python main.py offline   "берсерк"
    python main.py merge     data/mangalib_catalog.json data/remanga_catalog.json

Работает без установки: скрипт сам добавляет `src/` в sys.path.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows-консоль по умолчанию печатает в cp866/cp1251 и ломает кириллицу
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mangaparser.cli import main  # noqa: E402  (импорт после настройки sys.path)

if __name__ == "__main__":
    raise SystemExit(main())
