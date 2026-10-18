"""Кэш изображений: обложки, страницы глав и офлайн-копии.

Картинки тянутся `requests`-ом в фоновых потоках и кладутся на диск. Так
нужно по двум причинам:

* CDN источников отдают картинки только с `Referer` сайта — без него ReManga
  отвечает 403, а Flet сам этот заголовок не подставить;
* локальный файл показывается без повторной загрузки, поэтому повторное
  чтение главы мгновенное, а сама глава доступна без сети.

Два разных хранилища: `pages` — кэш прочитанного (общий, по хэшу URL),
`chapters` — явные скачивания (папка на главу, для списка «Скачанное»).
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ..core.http import Http, SourceError, build_session
from .paths import covers_dir
from .store import chapter_key

MAX_COVER_BYTES = 6 * 1024 * 1024
MAX_PAGE_BYTES = 24 * 1024 * 1024

#: Файл-метка: глава считается скачанной только если он на месте. Без него
#: прерванная закачка выглядела бы как готовая глава с неполным комплектом страниц.
MANIFEST = "chapter.json"


def _slug(value: str) -> str:
    """Имя каталога из произвольной строки: буквы, цифры, дефис."""
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return (cleaned or "x")[:80]


def _extension(url: str) -> str:
    tail = url.split("?")[0].rsplit("/", 1)[-1]
    if "." in tail:
        suffix = tail.rsplit(".", 1)[-1].lower()
        if suffix.isalnum() and 2 <= len(suffix) <= 4:
            return f".{suffix}"
    return ".jpg"


def _digest(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:24]


class ImageStore:
    """Дисковое хранилище изображений приложения."""

    def __init__(self, root: Path | None = None) -> None:
        self.covers = Path(root) if root else covers_dir()
        self.pages = self.covers.parent / "pages"
        self.chapters = self.covers.parent / "chapters"
        for path in (self.covers, self.pages, self.chapters):
            path.mkdir(parents=True, exist_ok=True)
        self._session = build_session()
        self._http = Http(self._session)
        self._lock = threading.Lock()

    # ---------- обложки ----------

    def cover_path(self, url: str) -> Path:
        return self.covers / f"{_digest(url)}{_extension(url)}"

    def cached_cover(self, url: str | None) -> str | None:
        if not url:
            return None
        path = self.cover_path(url)
        return str(path) if path.exists() and path.stat().st_size else None

    def fetch_cover(self, url: str | None, referer: str | None = None) -> str | None:
        """Скачать обложку и вернуть локальный путь (None, если не вышло)."""
        if not url:
            return None
        if cached := self.cached_cover(url):
            return cached
        data = self._download(url, MAX_COVER_BYTES, referer=referer)
        if not data:
            return None
        path = self.cover_path(url)
        self._write(path, data)
        return str(path)

    # ---------- кэш страниц ----------

    def page_path(self, url: str) -> Path:
        return self.pages / f"{_digest(url)}{_extension(url)}"

    def cached_page(self, url: str) -> str | None:
        path = self.page_path(url)
        return str(path) if path.exists() and path.stat().st_size else None

    def fetch_page(self, url: str, referer: str | None = None) -> str | None:
        """Скачать страницу в кэш и вернуть локальный путь."""
        if cached := self.cached_page(url):
            return cached
        try:
            data = self._download(url, MAX_PAGE_BYTES, referer=referer, required=True)
        except SourceError:
            return None
        if not data:
            return None
        path = self.page_path(url)
        self._write(path, data)
        return str(path)

    def fetch_in_background(
        self,
        urls: Sequence[str],
        referer: str | None = None,
        on_done: Callable[[str, str], None] | None = None,
    ) -> None:
        """Скачать страницы в фоне, докладывая каждую готовую."""
        import threading

        def worker() -> None:
            for url in urls:
                if self.cached_page(url):
                    continue
                if self.fetch_page(url, referer) and on_done:
                    on_done(url, url)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- офлайн-главы ----------

    def chapter_folder(self, source: str, key: str, chapter: Any) -> Path:
        path = self._chapter_dir(source, key, chapter)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _chapter_dir(self, source: str, key: str, chapter: Any) -> Path:
        return self.chapters / _slug(source) / _slug(key) / _slug(chapter_key(chapter))

    def local_chapter(self, source: str, key: str, chapter: Any) -> list[str]:
        """Локальные страницы главы; пусто, если глава не скачана целиком."""
        folder = self._chapter_dir(source, key, chapter)
        if not folder.is_dir() or not (folder / MANIFEST).exists():
            return []
        return [str(p) for p in sorted(folder.iterdir()) if p.suffix not in (".json", ".part")]

    def download_chapter(
        self,
        urls: Sequence[str],
        folder: Path,
        *,
        referer: str | None = None,
        on_progress: Callable[[int, int], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[str]:
        """
        Скачать страницы главы на диск, пропуская уже скачанные.

        `on_progress(done, total)` вызывается после каждой страницы,
        `should_stop()` — между страницами, чтобы отмену не ждал целый том.
        """
        folder.mkdir(parents=True, exist_ok=True)
        total = len(urls)
        saved: list[str] = []

        for done, url in enumerate(urls, 1):
            if should_stop and should_stop():
                break
            target = folder / f"{done:04d}{_extension(url)}"
            if target.exists() and target.stat().st_size:
                saved.append(str(target))
            else:
                data = self._download(url, MAX_PAGE_BYTES, referer=referer)
                if data:
                    self._write(target, data)
                    saved.append(str(target))
            if on_progress:
                on_progress(done, total)

        if saved and len(saved) == total:
            (folder / MANIFEST).write_text(f'{{"pages": {total}}}', encoding="utf-8")
        return saved

    def remove_chapter(self, source: str, key: str, chapter: Any) -> None:
        folder = self._chapter_dir(source, key, chapter)
        if not folder.is_dir():
            return
        for path in folder.iterdir():
            path.unlink(missing_ok=True)
        folder.rmdir()

    def remove_title(self, source: str, key: str) -> None:
        folder = self.chapters / _slug(source) / _slug(key)
        if not folder.is_dir():
            return
        for path in sorted(folder.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink(missing_ok=True)
        folder.rmdir()

    def cache_size(self) -> int:
        return sum(path.stat().st_size for path in self.pages.rglob("*") if path.is_file())

    # ---------- сеть ----------

    def _write(self, path: Path, data: bytes) -> None:
        with self._lock:
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(path)

    def _download(
        self,
        url: str,
        limit: int,
        referer: str | None = None,
        required: bool = False,
    ) -> bytes | None:
        """
        Скачать файл, при отказе CDN повторив с `Referer` сайта-источника.

        Часть сайтов (ReManga) отдаёт картинки только на запрос, у которого
        Referer указывает на их домен, поэтому заголовок обязателен.
        """
        headers = {"Referer": referer} if referer else {}
        for attempt in (headers, {}):
            try:
                response = self._session.get(
                    url, headers=headers or None, timeout=30, stream=True
                )
            except Exception as exc:
                if required and not attempt:
                    raise SourceError(f"Не удалось скачать: {url} ({exc})") from exc
                continue

            try:
                if response.status_code < 400:
                    return self._body(response, limit, url, required)
            finally:
                response.close()

        if required:
            raise SourceError(f"Картинка недоступна: {url}")
        return None

    def _body(
        self, response: Any, limit: int, url: str, required: bool
    ) -> bytes | None:
        chunks: list[bytes] = []
        size = 0
        for chunk in response.iter_content(64 * 1024):
            size += len(chunk)
            if size > limit:
                if required:
                    raise SourceError(f"Слишком большой файл: {url}")
                return None
            chunks.append(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        self._session.close()