"""Serper.dev Google Search бэкенд (PRIMARY).

Wire: POST https://google.serper.dev/search, X-API-KEY, {q, gl, hl, num}.
Ответ: organic[]{title, link, snippet, date?}. Vendor: docs/vendor/SEARCH_BACKENDS.md §1.
"""

import httpx

from app.search.base import (
    SEARCH_TIMEOUT,
    BackendUnavailableError,
    SearchBackendError,
    SearchOptions,
    SearchResult,
    hostname_of,
    parse_json,
    request_with_retry,
)

_URL = "https://google.serper.dev/search"


class SerperBackend:
    """Serper.dev: обычный Google organic поиск."""

    backend_id = "serper"

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
            raise BackendUnavailableError("serper: API key not configured")
        response = await request_with_retry(
            self._client,
            self.backend_id,
            "POST",
            _URL,
            headers={"X-API-KEY": self._api_key, "Content-Type": "application/json"},
            json={
                "q": query,
                "gl": options.country,
                "hl": options.language,
                "num": options.max_results,
            },
        )
        payload = parse_json(response, self.backend_id)
        if not isinstance(payload, dict):
            raise SearchBackendError(f"{self.backend_id}: unexpected payload (not an object)")
        organic = payload.get("organic")
        if organic is None:
            organic = []
        if not isinstance(organic, list):
            raise SearchBackendError(f"{self.backend_id}: unexpected 'organic' (not a list)")
        results: list[SearchResult] = []
        for item in organic[: options.max_results]:
            if not isinstance(item, dict):
                continue
            link = item.get("link")
            if not isinstance(link, str) or not link:
                continue
            title = item.get("title")
            snippet = item.get("snippet")
            date = item.get("date")
            results.append(
                SearchResult(
                    title=title if isinstance(title, str) else "",
                    url=link,
                    snippet=snippet if isinstance(snippet, str) else "",
                    source=hostname_of(link),
                    published_at=date if isinstance(date, str) else None,
                )
            )
        return results
