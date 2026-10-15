"""Порты — контракты, которые реализуют источники и хранилища.

Прикладной слой зависит только от этих протоколов, поэтому он не знает
ни про MangaLIB, ни про ReManga, и новый источник подключается без правок
application/cli.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Source(Protocol):
    """Источник каталога: поиск, страница каталога, карточка, главы."""

    name: str

    def catalog(self, page: int = 1, **kwargs: Any) -> list[Any]:
        """Одна страница каталога."""

    def search(self, query: str, page: int = 1, **kwargs: Any) -> list[Any]:
        """Серверный поиск по названию."""

    def details(self, key: str, **kwargs: Any) -> Any:
        """Карточка тайтла по ключу источника (slug/dir)."""

    def chapters(self, key: str, **kwargs: Any) -> Sequence[Any]:
        """Список глав."""

    def chapter_pages(self, key: str, chapter: Any, **kwargs: Any) -> list[str]:
        """
        URL страниц (картинок) главы — записи из `chapters()`.

        Ключ тайтла и сама глава передаются явно: у источников глава
        адресуется по-разному (id MangaLIB, `dir` ReManga, номер BuZuManga,
        адрес страницы SelfManga/InkStory), и запись из `chapters()` знает
        все нужные поля сама.
        """

    def iter_catalog(
        self,
        *,
        delay: float,
        max_pages: int | None = None,
        checkpoint: Checkpoint | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """Постраничный обход всего каталога, запись прогресса в checkpoint."""


@runtime_checkable
class Checkpoint(Protocol):
    """Хранилище промежуточного прогресса обхода (переживает краш)."""

    def append(self, rows: Sequence[dict[str, Any]]) -> None: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class OfflineIndex(Protocol):
    """Офлайн-поиск по локальной базе (мгновенно, без сети)."""

    def add(self, items: Sequence[Any]) -> None: ...

    def count(self) -> int: ...

    def search(self, query: str, limit: int = 60) -> list[Any]: ...


def default_out(source: str, root: Path, suffix: str = "catalog.json") -> Path:
    """Путь выгрузки по умолчанию: data/<source>_catalog.json."""
    return Path(root) / "data" / f"{source}_{suffix}"
