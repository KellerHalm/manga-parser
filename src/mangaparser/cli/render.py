"""Справка по источникам и командам."""

from __future__ import annotations

from ..sources import REGISTRY
from .app import COMMANDS

EXAMPLES = (
    "python main.py sources",
    'python main.py mangalib search "берсерк"',
    "python main.py remanga  dump --csv --max-pages 5",
    "python main.py inkstory catalog --page 3 --sort viewsCount",
    'python main.py desu     search "one piece"',
    'python main.py all     search naruto',
    "python main.py index --catalog data/remanga_catalog.json --reset",
    'python main.py offline "берсерк"',
    "python main.py merge data/mangalib_catalog.json data/remanga_catalog.json",
)


def print_sources(registry: dict) -> None:
    """Таблица источников: имя, описание, что умеет."""
    print("Источники:\n")
    for spec in registry.values():
        title, description = spec.describe()
        print(f"  {spec.name:10} {title:10} {description}")
    print("\nКоманды:")
    for name, help_text, _, _ in COMMANDS:
        print(f"  {name:10} {help_text}")
    print("  index      построить офлайн-индекс из выгрузок")
    print("  offline    поиск по офлайн-индексу")
    print("  merge      склеить выгрузки разных источников")
    print("\nПримеры:")
    for example in EXAMPLES:
        print(f"  {example}")


def print_usage() -> None:  # pragma: no cover - используется как fallback
    print_sources(REGISTRY)
