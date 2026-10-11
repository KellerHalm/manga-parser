"""Прикладной слой: сценарии использования, общие для всех источников.

Зависит только от портов `core.ports` и реализаций хранения, поэтому
не содержит ни одного упоминания конкретного сайта.
"""

from .browse import catalog_page, chapters, details, search
from .catalog import build_from_checkpoint, dump_catalog
from .enrich import enrich_catalog
from .index import build_index, search_index
from .merge import merge_catalogs
from .report import CollectingReporter, Reporter

__all__ = [
    "CollectingReporter",
    "Reporter",
    "build_from_checkpoint",
    "build_index",
    "catalog_page",
    "chapters",
    "details",
    "dump_catalog",
    "enrich_catalog",
    "merge_catalogs",
    "search",
    "search_index",
]
