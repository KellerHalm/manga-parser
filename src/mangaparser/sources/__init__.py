"""Источники каталогов манги. Каждый — отдельный пакет, общий порт Source."""

from .registry import ALL, REGISTRY, SourceSpec, clients, names, resolve, resolve_many

__all__ = ["ALL", "REGISTRY", "SourceSpec", "clients", "names", "resolve", "resolve_many"]
