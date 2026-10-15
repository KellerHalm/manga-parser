"""Реестр источников.

Единственное место, где перечислены парсеры: добавить новый источник —
создать пакет в `sources/` с функциями `build()` и `describe()` и вписать
его сюда. Прикладной слой и CLI об этом не знают.

Модули импортируются лениво: сломанный/недоступный источник не роняет
остальные.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from ..core.ports import Source


@dataclass(frozen=True)
class SourceSpec:
    """Описание источника для CLI и реестра."""

    name: str
    module: str
    title: str = ""
    description: str = ""

    def build(self) -> Source:
        module = importlib.import_module(self.module, __package__)
        return module.build()  # type: ignore[no-any-return]

    def describe(self) -> tuple[str, str]:
        module = importlib.import_module(self.module, __package__)
        describe: Callable[[], tuple[str, str]] = getattr(module, "describe", None)
        return describe() if describe else (self.name, "")


REGISTRY: dict[str, SourceSpec] = {
    spec.name: spec
    for spec in (
        SourceSpec("mangalib", "mangaparser.sources.mangalib.client"),
        SourceSpec("remanga", "mangaparser.sources.remanga.client"),
        SourceSpec("inkstory", "mangaparser.sources.inkstory.client"),
        SourceSpec("buzumanga", "mangaparser.sources.buzumanga.client"),
        SourceSpec("comx", "mangaparser.sources.comx.client"),
<<<<<<< HEAD
        SourceSpec("desu", "mangaparser.sources.desu.client"),
=======
        SourceSpec("selfmanga", "mangaparser.sources.selfmanga.client"),
>>>>>>> 2461edd3c0b5f630b93f4f89d696b9a40970d7d5
    )
}

#: Псевдоним «all» — команда выполняется для всех источников подряд.
ALL = "all"


def names() -> list[str]:
    return list(REGISTRY)


def resolve(name: str) -> SourceSpec:
    if name not in REGISTRY:
        raise KeyError(f"Неизвестный источник: {name}. Доступны: {', '.join(REGISTRY)}")
    return REGISTRY[name]


def resolve_many(name: str) -> list[SourceSpec]:
    """`all` -> все источники, иначе один конкретный."""
    if name == ALL:
        return list(REGISTRY.values())
    return [resolve(name)]


def clients(name: str) -> Iterator[Any]:
    for spec in resolve_many(name):
        yield spec.build()


__all__ = ["REGISTRY", "ALL", "SourceSpec", "names", "resolve", "resolve_many", "clients"]
