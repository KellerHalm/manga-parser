"""InkStory — SSR-разметка + JSON-LD (inkstory.net)."""

from . import parsing
from .client import Book, InkStoryClient, InkStoryError

__all__ = ["Book", "InkStoryClient", "InkStoryError", "parsing"]
