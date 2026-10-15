"""SelfManga — SSR-разметка /list, /search и /<slug> (1.selfmanga.live)."""

from . import parsing
from .client import Manga, SelfMangaClient, SelfMangaError, chapter_url, slug_of

__all__ = ["Manga", "SelfMangaClient", "SelfMangaError", "slug_of", "chapter_url", "parsing"]
