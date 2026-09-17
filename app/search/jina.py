"""Jina AI: s.jina.ai — поиск, r.jina.ai — reader.

Search: GET https://s.jina.ai/?q=..., Authorization: Bearer (ключ обязателен),
Accept: application/json → до 5 записей {url, title, content, timestamp}.
Reader: GET https://r.jina.ai/<url>, Accept: application/json, X-Timeout (с).
Vendor: docs/vendor/SEARCH_BACKENDS.md §4.
"""

from typing import Any

import httpx

from app.search.base import (
    LONG_TIMEOUT,
    SEARCH_TIMEOUT,
    BackendUnavailableError,
    SearchOptions,
    SearchResult,
    hostname_of,
    parse_json,
    request_with_retry,
)

_SEARCH_URL = "https://s.jina.ai/"
_READER_URL = "https://r.jina.ai/"
_SNIPPET_CHARS = 300


class JinaSearchBackend:
    """Jina search (s.jina.ai): топ-5 с полным контентом."""

    backend_id = "jina_search"

    def __init__(
        self, api_key: str | None = None, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self._api_key = api_key
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(trust_env=False, timeout=SEARCH_TIMEOUT)

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, query: str, options: SearchOptions) -> list[SearchResult]:
        if not self._api_key:
            raise BackendUnavailableError("jina_search: API key not configured")
        response = await request_with_retry(
            self._client,
            self.backend_id,
            "GET",
            _SEARCH_URL,
            params={"q": query},
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Accept": "application/json",
            },
        )
        payload = parse_json(response, self.backend_id)
        entries: list[Any]
        if isinstance(payload, dict):
            data = payload.get("data")
            entries = data if isinstance(data, list) else []
        elif isinstance(payload, list):
            entries = payload
        else:
            entries = []
        results: list[SearchResult] = []
        for entry in entries[: options.max_results]:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not url:
                continue
            content = entry.get("content") or ""
            results.append(
                SearchResult(
                    title=entry.get("title") or "",
                    url=url,
                    snippet=content[:_SNIPPET_CHARS],
                    source=hostname_of(url),
                    published_at=entry.get("timestamp"),
                )
            )
        return results


class JinaReaderBackend:
    """Jina reader (r.jina.ai): URL → текст. Без ключа работает (20 RPM)."""

    backend_id = "jina_reader"

    def __init__(
        self, api_key: str | None = None, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self._api_key = api_key
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(trust_env=False, timeout=LONG_TIMEOUT)

    def is_configured(self) -> bool:
        return True  # reader допустим и без ключа (низкий rate limit)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def read(
        self,
        url: str,
        *,
        timeout: float = 30.0,  # noqa: ASYNC109  # per-request httpx timeout — контракт F1
        max_chars: int = 8000,
    ) -> str | None:
        """Прочитать URL через r.jina.ai; None при любой ошибке (caller fallback'ит)."""
        headers = {"Accept": "application/json", "X-Timeout": str(int(timeout))}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            response = await self._client.get(
                f"{_READER_URL}{url}",
                headers=headers,
                timeout=httpx.Timeout(connect=5.0, read=timeout, write=timeout, pool=timeout),
            )
            if response.status_code >= 400:
                return None
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        content: Any = None
        if isinstance(payload, dict):
            content = payload.get("content")
            data = payload.get("data")
            if content is None and isinstance(data, dict):
                content = data.get("content")
        if not isinstance(content, str) or not content.strip():
            return None
        return content[:max_chars]
