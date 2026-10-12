"""BuZuManga — JSON API сайта (buzumanga.com)."""

from . import slug
from .client import BuZuMangaClient, BuZuMangaError, Manga, image_url, manga_id

__all__ = ["Manga", "BuZuMangaClient", "BuZuMangaError", "image_url", "manga_id", "slug"]