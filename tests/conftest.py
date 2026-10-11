"""Общие фикстуры: фейковый HTTP без сети."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


class FakeResponse:
    """Минимальный аналог requests.Response."""

    def __init__(self, payload: Any = None, text: str = "", status_code: int = 200, url: str = "") -> None:
        self._payload = payload
        self.text = text or ("" if payload is None else str(payload))
        self.status_code = status_code
        self.url = url

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}", response=self)


class FakeSession:
    """Отдаёт заранее заданные ответы и запоминает запросы."""

    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, params: dict[str, Any] | None = None, **_: Any) -> FakeResponse:
        self.calls.append((url, dict(params or {})))
        for fragment, response in self.responses.items():
            if fragment in url:
                if callable(response):
                    return response(url, params or {})
                return response
        return FakeResponse(payload={}, url=url)

    def mount(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        pass


@pytest.fixture()
def fake_session_factory():
    def make(responses: dict[str, Any]) -> FakeSession:
        return FakeSession(responses)

    return make
