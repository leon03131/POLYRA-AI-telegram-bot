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
        text_blocks = self._text_blocks(ai_overview)
        if not text_blocks:
            page_token = ai_overview.get("page_token")
            if not isinstance(page_token, str) or not page_token:
                return self._degrade(step1, options)
            step2 = await self._get_json({"engine": "google_ai_overview", "page_token": page_token})
            ai_overview = step2.get("ai_overview")
            if not isinstance(ai_overview, dict) or ai_overview.get("error"):
                return self._degrade(step1, options)
            text_blocks = self._text_blocks(ai_overview)
        overview_text = self._overview_text(text_blocks)
        references = self._references(ai_overview, options)
        if not references:
            # A32: overview без references НЕ авторитетен — не выдаём текст,
            # деградируем в обычный organic.
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
        raw_items = payload.get("organic_results")
        if raw_items is None:
            return []
        if not isinstance(raw_items, list):
            raise SearchBackendError(
                f"{self.backend_id}: unexpected 'organic_results' (not a list)"
            )
        results: list[SearchResult] = []
        for item in raw_items[: options.max_results]:
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

    def _text_blocks(self, ai_overview: dict[str, Any]) -> list[Any]:
        raw = ai_overview.get("text_blocks")
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise SearchBackendError(f"{self.backend_id}: unexpected 'text_blocks' (not a list)")
        return raw

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
        raw_refs = ai_overview.get("references")
        if raw_refs is None:
            return []
        if not isinstance(raw_refs, list):
            raise SearchBackendError("serpapi_aio: unexpected 'references' (not a list)")
        results: list[SearchResult] = []
        for ref in raw_refs:
            if not isinstance(ref, dict):
                continue
            link = ref.get("link")
            if not isinstance(link, str) or not link:
                continue
            title = ref.get("title")
            snippet = ref.get("snippet")
            source = ref.get("source")
            results.append(
                SearchResult(
                    title=title if isinstance(title, str) else "",
                    url=link,
                    snippet=snippet if isinstance(snippet, str) else "",
                    source=(source if isinstance(source, str) and source else None)
                    or hostname_of(link),
                )
            )
            if len(results) >= options.max_results:
                break
        return results
