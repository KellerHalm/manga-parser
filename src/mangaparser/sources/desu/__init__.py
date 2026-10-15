"""Desu.Me — парсер каталога манг (desu.uno)."""

from . import parsing
from .client import DesuClient, DesuError, Title

__all__ = ["Title", "DesuClient", "DesuError", "parsing"]