"""Brave Search API бэкенд (optional).

Wire: GET https://api.search.brave.com/res/v1/web/search, X-Subscription-Token.
Ответ: web.results[]{title, url, description(HTML — strip!), page_age?, profile{}}.
Vendor: docs/vendor/SEARCH_BACKENDS.md §3.
"""

from typing import Any

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
        items = self._result_items(parse_json(response, self.backend_id))
        results: list[SearchResult] = []
        for item in items[: options.max_results]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url:
                continue
            profile = item.get("profile")
            if not isinstance(profile, dict):
                profile = {}
            title = item.get("title")
            description = item.get("description")
            page_age = item.get("page_age")
            profile_name = profile.get("name")
            if not isinstance(profile_name, str) or not profile_name:
                profile_name = hostname_of(url)
            results.append(
                SearchResult(
                    title=title if isinstance(title, str) else "",
                    url=url,
                    snippet=strip_html(description if isinstance(description, str) else ""),
                    source=profile_name,
                    published_at=page_age if isinstance(page_age, str) else None,
                )
            )
        return results

    def _result_items(self, payload: object) -> list[Any]:
        """payload.web.results с проверкой типов; невалидная структура → SearchBackendError."""
        if not isinstance(payload, dict):
            raise SearchBackendError(f"{self.backend_id}: unexpected payload (not an object)")
        web = payload.get("web")
        if web is None:
            return []
        if not isinstance(web, dict):
            raise SearchBackendError(f"{self.backend_id}: unexpected 'web' (not an object)")
        raw_items = web.get("results")
        if raw_items is None:
            return []
        if not isinstance(raw_items, list):
            raise SearchBackendError(f"{self.backend_id}: unexpected 'web.results' (not a list)")
        return raw_items
