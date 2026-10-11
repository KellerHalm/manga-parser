"""Отчёт о ходе обхода: единый формат сообщений для всех источников.

Источники не печатают ничего сами — они вызывают колбэк `on_page`, а
форматирование живёт здесь. Так сообщения одинаковы у всех парсеров.
"""

from __future__ import annotations

import sys
import time
from typing import Any, TextIO


class Reporter:
    """Пишет прогресс в stderr, результаты — в stdout."""

    def __init__(self, stream: TextIO | None = None, quiet: bool = False) -> None:
        self.stream = stream or sys.stderr
        self.quiet = quiet
        self._started = time.perf_counter()

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, file=self.stream, flush=True)

    def warn(self, message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    def result(self, message: str) -> None:
        print(message, flush=True)

    def elapsed(self) -> float:
        return time.perf_counter() - self._started

    def on_page(self, event: str, *args: Any) -> None:
        """Колбэк, который передаётся в `source.iter_catalog`."""
        if event == "page":
            page, added, total = args
            self.info(f"  [{page}] +{added} (всего {total})")
        elif event == "rate-limited":
            self.warn(f"[!] 429 на стр. {args[0]} — пауза увеличена")
        elif event in {"empty", "duplicates", "end"}:
            self.info(f"[i] стр. {args[0] if args else '?'}: {event} — обход завершён")
        elif event == "end-with-reason":
            self.info(f"[i] {args[1] if len(args) > 1 else ''}")


class CollectingReporter(Reporter):
    """Reporter для тестов и библиотечного использования: ничего не печатает."""

    def __init__(self) -> None:
        super().__init__(quiet=True)
        self.events: list[tuple[str, tuple[Any, ...]]] = []

    def on_page(self, event: str, *args: Any) -> None:
        self.events.append((event, args))

    @property
    def pages(self) -> int:
        return sum(1 for event, _ in self.events if event == "page")
