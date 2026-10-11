"""CLI-слой: разбор аргументов и вызов прикладных сценариев."""

from .app import build_parser, main, split_source

__all__ = ["build_parser", "main", "split_source"]
