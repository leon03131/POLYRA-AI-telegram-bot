"""Общие типы, ошибки и HTTP-helper'ы поисковых бэкендов (F1).

Модель данных и контракты: docs/vendor/SEARCH_BACKENDS.md (2026-09-18).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx

SEARCH_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=10.0)
LONG_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=10.0)

_MAX_ATTEMPTS = 2  # 1 исходная попытка + 1 повтор (только 429/5xx/timeout)
_RETRY_BACKOFF_SECONDS = 0.3

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Единый результат поиска (snippet — plain text)."""

    title: str
    url: str
    snippet: str
    source: str | None = None
    published_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SearchOptions:
    """Параметры запроса; mode: normal | ai_overview | auto."""

    max_results: int = 5
    language: str = "ru"
    country: str = "ru"
    mode: str = "normal"


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """Итог поиска: результаты + какой бэкенд ответил (+ текст AI Overview)."""

    results: list[SearchResult]
    backend: str
    ai_overview_text: str | None = None


class SearchBackendError(Exception):
    """Ошибка поискового бэкенда; retryable=True — имеет смысл повторить."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class BackendUnavailableError(SearchBackendError):
    """Бэкенд недоступен (нет ключа / disabled / captcha / 401/403) → fallback."""


class SearchBackend(Protocol):
    """Контракт поискового бэкенда. aclose — lifecycle (A30): вызывается менеджером
    при shutdown и при смене конфига/ключа бэкенда."""

    backend_id: str

    async def search(self, query: str, options: SearchOptions) -> list[SearchResult]: ...

    def is_configured(self) -> bool: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class SupportsAIOverview(Protocol):
    """Бэкенд, умеющий режим AI Overview (SerpApi)."""

    backend_id: str

    def is_configured(self) -> bool: ...

    async def ai_overview(self, query: str, options: SearchOptions) -> SearchOutcome: ...


def strip_html(text: str) -> str:
    """Убрать HTML-теги (Brave description содержит <strong>) и схлопнуть пробелы."""
    return " ".join(_TAG_RE.sub(" ", text).split())


def hostname_of(url: str) -> str | None:
    """Hostname URL'а для SearchResult.source; None, если не удалось распарсить."""
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


def parse_json(response: httpx.Response, backend_id: str) -> Any:
    """Распарсить JSON ответа; невалидный JSON → SearchBackendError."""
    try:
        return response.json()
    except ValueError as exc:  # JSONDecodeError и UnicodeDecodeError — подклассы ValueError
        raise SearchBackendError(f"{backend_id}: invalid JSON response") from exc


async def request_with_retry(
    client: httpx.AsyncClient,
    backend_id: str,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """HTTP-запрос с классификацией ошибок проекта и одним повтором.

    401/403 → BackendUnavailableError; 429/5xx/timeout → 1 повтор, затем
    SearchBackendError(retryable=True); прочие 4xx → SearchBackendError.
    """
    last_error: SearchBackendError | None = None
    for attempt in range(_MAX_ATTEMPTS):
        if attempt:
            await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TimeoutException as exc:
            last_error = SearchBackendError(
                f"{backend_id}: timeout ({exc.__class__.__name__})", retryable=True
            )
            continue
        except httpx.HTTPError as exc:
            raise SearchBackendError(
                f"{backend_id}: network error ({exc.__class__.__name__})"
            ) from exc
        if response.status_code in (401, 403):
            raise BackendUnavailableError(f"{backend_id}: HTTP {response.status_code} (auth)")
        if response.status_code == 429 or response.status_code >= 500:
            last_error = SearchBackendError(
                f"{backend_id}: HTTP {response.status_code}", retryable=True
            )
            continue
        if response.status_code >= 400:
            raise SearchBackendError(f"{backend_id}: HTTP {response.status_code}")
        return response
    if last_error is None:  # pragma: no cover - недостижимо (всегда >= 1 попытки)
        last_error = SearchBackendError(f"{backend_id}: request failed")
    raise last_error
