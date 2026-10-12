"""Транслитерация названия в slug для адреса страницы тайтла.

BuzuManga адресует карточку как `/manga/<id>-<slug>`, но в JSON API слага нет —
он есть только в human-readable URL. Чтобы `url` в выгрузке был кликабельным,
slug собирается из названия по правилам сайта (проверено на примере
`Мастера присматривают за мной` -> `mastera-prismatrivayut-za-mnoj`).

Идентичность тайтла при этом определяется числовым `id`, а не slug: адресация
в API идёт по `/api/manga/{id}`, поэтому расхождение транслитерации ничего
не ломает.
"""

from __future__ import annotations

import re
import unicodedata

#: Кириллица -> латиница (согласные пишутся как в ЧПУ сайта: ж->zh, ц->ts, ш->sh).
_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

_SEPARATORS = re.compile(r"[^a-z0-9]+")

#: Диакритика после NFKD («é» -> «e» + «́»).
_MARKS = re.compile(r"[\u0300-\u036f]")


def slugify(title: str) -> str:
    """«Мастера присматривают за мной» -> `mastera-prismatrivayut-za-mnoj`."""
    # NFKD здесь неприменим: он разбирает «й» на «и» + breve, и «й» уходит
    # в «i» вместо «j». Поэтому сначала кириллица, потом акценты латиницы.
    folded = title.casefold()
    mapped = "".join(_MAP.get(ch, ch) for ch in folded)
    latin = _MARKS.sub("", unicodedata.normalize("NFKD", mapped))
    return _SEPARATORS.sub("-", "".join(ch for ch in latin if ch.isascii())).strip("-")