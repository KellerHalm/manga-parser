"""Мелкие утилиты разбора данных: HTML -> текст, строки -> числа."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from typing import Any, TypeVar

_NUM_RE = re.compile(r"\d+[.,]?\d*")

T = TypeVar("T")


def strip_tags(raw: str) -> str:
    """Убирает теги, раскрывает HTML-сущности, схлопывает пробелы."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw or ""))
    return re.sub(r"\s+", " ", text).strip()


def page_text(page: str) -> str:
    """Видимый текст HTML-страницы без скриптов и стилей."""
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S | re.I)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cleaned))).strip()


def to_float(raw: Any) -> float | None:
    """«7,5» / «7.5 rating» / 7.5 -> float. None, если числа нет."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    match = _NUM_RE.search(str(raw).replace("\u00a0", " "))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def to_int(raw: Any) -> int | None:
    """«1 234 просмотра» -> 1234. None, если цифр нет."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    digits = re.sub(r"[^\d]", "", str(raw))
    return int(digits) if digits else None


def label(value: Any, key: str = "label") -> str | None:
    """API отдаёт часть полей объектом {id, label/name} — достаём строку."""
    if isinstance(value, dict):
        return value.get(key) or value.get("label") or value.get("name")
    return value


def key_of(row: Any, key: str) -> Any:
    """Ключ дедупликации для dict-записи или dataclass-объекта."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def dedupe(rows: Iterable[T], key: str) -> list[T]:
    """Убирает повторы по ключу, сохраняя порядок первого появления."""
    seen: set[Any] = set()
    unique: list[T] = []
    for row in rows:
        value = key_of(row, key)
        if value in seen:
            continue
        seen.add(value)
        unique.append(row)
    return unique
