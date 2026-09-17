"""SerpApi: Google organic + двухшаговый AI Overview.

Wire: GET https://serpapi.com/search.json.
Шаг 1: engine=google&q → ai_overview: text_blocks+references | page_token | error.
Шаг 2 (если page_token, TTL ≤ 60 с): engine=google_ai_overview&page_token.
ai_overview.error — отсутствие обзора, НЕ ошибка клиента: деградация в organic.
Vendor: docs/vendor/SEARCH_BACKENDS.md §2.
"""

from typing import Any

import httpx

from app.search.base import (
    LONG_TIMEOUT,
    BackendUnavailableError,
    SearchBackendError,
    SearchOptions,
    SearchOutcome,
    SearchResult,
    hostname_of,
    parse_json,
    request_with_retry,
)

_URL = "https://serpapi.com/search.json"


class SerpApiAIOverviewBackend:
    """SerpApi: и обычный поиск (engine=google organic), и режим ai_overview."""

    backend_id = "serpapi_aio"

    def __init__(
        self, api_key: str | None = None, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self._api_key = api_key
        self._owns_client = http_client is None
        # ai_overview — до 30 с read (docs/vendor/SEARCH_BACKENDS.md, таймауты)
        self._client = http_client or httpx.AsyncClient(trust_env=False, timeout=LONG_TIMEOUT)

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, query: str, options: SearchOptions) -> list[SearchResult]:
        """Обычный поиск: engine=google, organic_results → SearchResult."""
        if not self._api_key:
            raise BackendUnavailableError("serpapi_aio: API key not configured")
        payload = await self._get_json(
            {"engine": "google", "q": query, "hl": options.language, "gl": options.country}
        )
        return self._organic(payload, options)

    async def ai_overview(self, query: str, options: SearchOptions) -> SearchOutcome:
        """Двухшаговый AI Overview; при отсутствии обзора — organic без ошибки."""
        if not self._api_key:
            raise BackendUnavailableError("serpapi_aio: API key not configured")
        step1 = await self._get_json(
            {"engine": "google", "q": query, "hl": options.language, "gl": options.country}
        )
        ai_overview = step1.get("ai_overview")
        if not isinstance(ai_overview, dict) or ai_overview.get("error"):
            return self._degrade(step1, options)
        text_blocks = ai_overview.get("text_blocks") or []
        if not text_blocks:
            page_token = ai_overview.get("page_token")
            if not page_token:
                return self._degrade(step1, options)
            step2 = await self._get_json({"engine": "google_ai_overview", "page_token": page_token})
            ai_overview = step2.get("ai_overview")
            if not isinstance(ai_overview, dict) or ai_overview.get("error"):
                return self._degrade(step1, options)
            text_blocks = ai_overview.get("text_blocks") or []
        overview_text = self._overview_text(text_blocks)
        references = self._references(ai_overview, options)
        if overview_text is None and not references:
            return self._degrade(step1, options)
        return SearchOutcome(
            results=references,
            backend=self.backend_id,
            ai_overview_text=overview_text,
        )

    async def _get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        response = await request_with_retry(
            self._client,
            self.backend_id,
            "GET",
            _URL,
            params={**params, "api_key": self._api_key},
        )
        payload = parse_json(response, self.backend_id)
        if not isinstance(payload, dict):
            raise SearchBackendError(f"{self.backend_id}: unexpected payload (not an object)")
        return payload

    def _degrade(self, step1: dict[str, Any], options: SearchOptions) -> SearchOutcome:
        """Нет AI Overview (error/пусто) — валидная деградация в organic_results."""
        return SearchOutcome(results=self._organic(step1, options), backend=self.backend_id)

    def _organic(self, payload: dict[str, Any], options: SearchOptions) -> list[SearchResult]:
        results: list[SearchResult] = []
        for item in (payload.get("organic_results") or [])[: options.max_results]:
            link = item.get("link")
            if not link:
                continue
            results.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=link,
                    snippet=item.get("snippet") or "",
                    source=hostname_of(link),
                    published_at=item.get("date"),
                )
            )
        return results

    @staticmethod
    def _overview_text(text_blocks: list[Any]) -> str | None:
        """text_blocks (paragraph/heading/list snippets) → единый текст через \\n."""
        parts: list[str] = []
        for block in text_blocks:
            if not isinstance(block, dict):
                continue
            snippet = block.get("snippet")
            if isinstance(snippet, str) and snippet.strip():
                parts.append(snippet.strip())
            items = block.get("list")
            if isinstance(items, list):
                for entry in items:
                    if not isinstance(entry, dict):
                        continue
                    entry_text = entry.get("snippet") or entry.get("title")
                    if isinstance(entry_text, str) and entry_text.strip():
                        parts.append(entry_text.strip())
        return "\n".join(parts) or None

    @staticmethod
    def _references(ai_overview: dict[str, Any], options: SearchOptions) -> list[SearchResult]:
        results: list[SearchResult] = []
        for ref in ai_overview.get("references") or []:
            if not isinstance(ref, dict):
                continue
            link = ref.get("link")
            if not link:
                continue
            results.append(
                SearchResult(
                    title=ref.get("title") or "",
                    url=link,
                    snippet=ref.get("snippet") or "",
                    source=ref.get("source") or hostname_of(link),
                )
            )
            if len(results) >= options.max_results:
                break
        return results
