"""Com-X — парсер открытых sitemap (com-x.life)."""

from . import parsing
from .client import ComXClient, ComXError, Title

__all__ = ["Title", "ComXClient", "ComXError", "parsing"]