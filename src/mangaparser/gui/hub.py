"""Доступ к источникам из GUI.

Клиенты источников синхронные (`requests`), а Flet обновляет кадры из
одного потока. Поэтому здесь два правила:

* сетевой вызов идёт через `page.run_thread`, то есть в отдельном потоке;
* один источник обслуживается одним потоком за раз — сессия `requests`
  не потокобезопасна, а троттлинг источника рассчитан на последовательные
  запросы. Разные источники при этом работают параллельно.

Заодно общий доступ к данным тайтла: у источников поля называются по-разному
(`name`/`main_name`/`title`), и экран не должен знать об этом.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

from ..core.models import chapter_field
from ..sources import registry
from ..sources.registry import ALL


class SourceHub:
    """Ленивый реестр клиентов источников: строит клиент один раз и держит."""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def names(self) -> list[str]:
        return registry.names()

    def lock(self, name: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(name, threading.Lock())

    def client(self, name: str) -> Any:
        with self._guard:
            client = self._clients.get(name)
            if client is None:
                client = registry.resolve(name).build()
                self._clients[name] = client
            return client

    def warm(self, names: Sequence[str] | None = None) -> None:
        """Заранее создать клиентов: первый тап не ждёт импорта модуля."""
        for name in names or self.names():
            try:
                self.client(name)
            except Exception:
                continue


# ---------- чтение полей записи тайтла ----------


def get(item: Any, *names: str, default: Any = None) -> Any:
    """Первое непустое поле записи из списка имён."""
    for name in names:
        value = chapter_field(item, name, None)
        if value not in (None, "", [], {}):
            return value
    return default


def title_of(item: Any) -> str:
    return str(get(item, "title", "name", "main_name", "alt_name", default="Без названия"))


def cover_of(item: Any) -> str | None:
    return get(item, "cover", "cover_url", "image", "img", "poster", "thumb")


def rating_of(item: Any) -> float | None:
    value = get(item, "rating", "score", "rate")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def status_of(item: Any) -> str | None:
    value = get(item, "status", "translate_status", "status_translation")
    return str(value) if value else None


def genres_of(item: Any) -> list[str]:
    for name in ("genres", "tags", "categories", "tags_list"):
        value = get(item, name)
        if isinstance(value, dict):
            return [str(v) for v in value.values()]
        if isinstance(value, (list, tuple)):
            return [str(v.get("name") if isinstance(v, dict) else v) for v in value]
    return []


def key_of(item: Any) -> str:
    """Ключ тайтла у источника: slug/dir/id."""
    value = get(item, "key")
    if value:
        return str(value)
    for name in ("slug", "dir", "url", "uid", "id"):
        value = get(item, name)
        if value:
            return str(value)
    return title_of(item)


def author_of(item: Any) -> str | None:
    authors = get(item, "authors", "people", "author", "artist")
    if isinstance(authors, (list, tuple)):
        names = []
        for entry in authors:
            if isinstance(entry, dict):
                names.append(str(entry.get("name") or entry.get("title") or ""))
            elif entry:
                names.append(str(entry))
        joined = ", ".join(n for n in names if n)
        return joined or None
    return str(authors) if authors else None


def description_of(item: Any) -> str | None:
    value = get(item, "description", "summary", "about", "annotation", "short_description")
    if not value:
        return None
    return str(value)


def site_of(hub: SourceHub, source: str) -> str | None:
    """
    Адрес сайта источника — как `Referer` для картинок.

    CDN большинства площадок отдаёт изображение только если запрос пришёл
    со страницы их сайта, поэтому без этого обложки и страницы отдают 403.
    Константа `SITE` есть у каждого модуля источника.
    """
    import sys

    try:
        client = hub.client(source)
    except Exception:
        return None
    module = sys.modules.get(type(client).__module__)
    return getattr(module, "SITE", None)


# ---------- поиск по нескольким источникам ----------


def search_all(
    hub: SourceHub,
    query: str,
    *,
    on_result: Callable[[str, list[Any]], None],
    on_error: Callable[[str, str], None],
    on_done: Callable[[], None],
    sources: Sequence[str] | None = None,
) -> None:
    """
    Поиск по всем источникам параллельно, результат приходит как готов.

    Каждый источник докладывает свою выдачу сам, поэтому экран наполняется
    по мере ответов, а не после самого медленного сайта.
    """
    import threading

    names = list(sources or hub.names())
    if ALL in names:
        names = hub.names()
    remaining = len(names)
    done_lock = threading.Lock()

    def worker(name: str) -> None:
        nonlocal remaining
        try:
            items = hub.client(name).search(query)
            on_result(name, list(items))
        except Exception as exc:
            on_error(name, str(exc))
        finally:
            with done_lock:
                remaining -= 1
                if remaining == 0:
                    on_done()

    for name in names:
        threading.Thread(target=worker, args=(name,), daemon=True).start()