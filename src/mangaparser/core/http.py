"""HTTP-слой: единая сессия с ретраями и адаптивный троттлинг.

Ни один источник не создаёт `requests.Session` сам — вся сетевая логика
(заголовки, ретраи, паузы, реакция на 429) сосредоточена здесь.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

RETRY_STATUSES: tuple[int, ...] = (429, 500, 502, 503, 504)


class SourceError(RuntimeError):
    """Базовая ошибка источника (сети или разбора ответа)."""


class Http:
    """Тонкая обёртка над requests.Session с общей обработкой ошибок."""

    def __init__(
        self,
        session: requests.Session,
        error: type[SourceError] = SourceError,
        allow_redirects: bool = True,
        timeout: int = 30,
    ) -> None:
        self.session = session
        self.error = error
        self.allow_redirects = allow_redirects
        self.timeout = timeout

    def get(self, url: str, params: Mapping[str, Any] | None = None) -> requests.Response:
        try:
            response = self.session.get(
                url,
                params=params,
                timeout=self.timeout,
                allow_redirects=self.allow_redirects,
            )
        except requests.RequestException as exc:
            raise self.error(f"Запрос не удался: {url} ({exc})") from exc

        if response.status_code == 404:
            raise self.error(f"Не найдено: {response.url}")
        if response.status_code == 422:
            raise self.error(f"Некорректные параметры: {response.url} -> {response.text[:200]}")
        if response.status_code >= 400:
            # Любой другой код ответа (401, 451 «закрыто по требованию
            # правообладателя», лимит, бан) приходит тем же SourceError:
            # обход каталога и enrich ловят именно его, пропускают запись и
            # идут дальше, а сырое HTTPError обрывало бы весь обход.
            raise self.error(f"Ответ {response.status_code}: {response.url}")
        return response

    def get_json(self, url: str, params: Mapping[str, Any] | None = None) -> Any:
        return self.get(url, params).json()

    def get_text(self, url: str, params: Mapping[str, Any] | None = None) -> str:
        return self.get(url, params).text


def build_session(
    *,
    headers: Mapping[str, str] | None = None,
    retry_statuses: Iterable[int] = RETRY_STATUSES,
    total: int = 4,
    backoff_factor: float = 1.5,
    pool_size: int = 4,
) -> requests.Session:
    """Сессия с User-Agent, ретраями на 5xx/429 и пулом соединений."""
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    if headers:
        session.headers.update(headers)

    retry = Retry(
        total=total,
        backoff_factor=backoff_factor,
        status_forcelist=tuple(retry_statuses),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=pool_size, pool_maxsize=pool_size)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class Throttle:
    """Пауза между запросами с автоматическим удвоением при 429.

    Заменяет копипаст проверки 429 в каждом обходе каталога.
    """

    def __init__(self, delay: float, max_delay: float = 60.0) -> None:
        self.base_delay = delay
        self.current = delay
        self.max_delay = max_delay

    def wait(self) -> None:
        time.sleep(self.current)

    def on_success(self) -> None:
        self.current = self.base_delay

    def on_rate_limit(self) -> float:
        """Удваивает паузу и возвращает новое значение (секунды)."""
        self.current = min(self.current * 2, self.max_delay)
        return self.current
