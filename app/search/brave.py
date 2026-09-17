"""Brave Search API бэкенд (optional).

Wire: GET https://api.search.brave.com/res/v1/web/search, X-Subscription-Token.
Ответ: web.results[]{title, url, description(HTML — strip!), page_age?, profile{}}.
Vendor: docs/vendor/SEARCH_BACKENDS.md §3.
"""

import httpx

from app.search.base import (
    SEARCH_TIMEOUT,
    BackendUnavailableError,
    SearchOptions,
    SearchResult,
    hostname_of,
    parse_json,
    request_with_retry,
    strip_html,
)

_URL = "https://api.search.brave.com/res/v1/web/search"
_MAX_COUNT = 20  # лимит API


class BraveSearchBackend:
    """Brave Search: web-результаты, description приходит с HTML-тегами."""

    backend_id = "brave"

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
            raise BackendUnavailableError("brave: API key not configured")
        response = await request_with_retry(
            self._client,
            self.backend_id,
            "GET",
            _URL,
            headers={"X-Subscription-Token": self._api_key, "Accept": "application/json"},
            params={
                "q": query,
                "count": min(max(options.max_results, 1), _MAX_COUNT),
                "country": options.country,
                "search_lang": options.language,
            },
        )
        payload = parse_json(response, self.backend_id)
        web = payload.get("web") if isinstance(payload, dict) else None
        items = (web or {}).get("results") or []
        results: list[SearchResult] = []
        for item in items[: options.max_results]:
            url = item.get("url")
            if not url:
                continue
            profile = item.get("profile") or {}
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=url,
                    snippet=strip_html(item.get("description") or ""),
                    source=profile.get("name") or hostname_of(url),
                    published_at=item.get("page_age"),
                )
            )
        return results
