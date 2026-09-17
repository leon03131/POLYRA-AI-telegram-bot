"""EXPERIMENTAL: локальный Chromium + парсинг Google SERP (disabled by default).

is_configured() → False, если playwright не установлен (зависимость опциональна,
в requirements НЕ добавляем). Запрещено: обход CAPTCHA, stealth, логин в Google;
при /sorry/ или recaptcha → BackendUnavailableError (fallback на следующий бэкенд).
Vendor: docs/vendor/SEARCH_BACKENDS.md §5.
"""

import logging
from typing import Any
from urllib.parse import quote_plus

from app.search.base import (
    BackendUnavailableError,
    SearchBackendError,
    SearchOptions,
    SearchResult,
    hostname_of,
)

logger = logging.getLogger(__name__)

_NAVIGATION_TIMEOUT_MS = 15000


class PlaywrightGoogleBackend:
    """Локальный Chromium, обычный SERP-парсинг. api_key игнорируется (uniform factory)."""

    backend_id = "playwright_google"

    def __init__(self, api_key: str | None = None) -> None:
        try:
            import playwright  # noqa: F401
        except ImportError:
            self._available = False
        else:
            self._available = True

    def is_configured(self) -> bool:
        return self._available

    async def aclose(self) -> None:
        return None

    async def search(self, query: str, options: SearchOptions) -> list[SearchResult]:
        if not self._available:
            raise BackendUnavailableError("playwright_google: playwright is not installed")
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BackendUnavailableError("playwright_google: playwright is not installed") from exc
        url = (
            "https://www.google.com/search?q="
            + quote_plus(query)
            + f"&hl={options.language}&gl={options.country}"
            + f"&num={options.max_results}"
        )
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                try:
                    page = await browser.new_page()
                    response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=_NAVIGATION_TIMEOUT_MS
                    )
                    if response is not None and response.status in (429, 503):
                        raise BackendUnavailableError(f"playwright_google: HTTP {response.status}")
                    captcha = "/sorry/" in page.url or await page.query_selector(
                        "iframe[src*='recaptcha']"
                    )
                    if captcha:
                        raise BackendUnavailableError("playwright_google: captcha detected")
                    return await self._parse_serp(page, options)
                finally:
                    await browser.close()
        except BackendUnavailableError:
            raise
        except Exception as exc:
            raise SearchBackendError(f"playwright_google: {exc.__class__.__name__}: {exc}") from exc

    async def _parse_serp(self, page: Any, options: SearchOptions) -> list[SearchResult]:
        """Минимальный SERP-парсинг: a:has(h3), поддержка /url?q= редирект-ссылок."""
        results: list[SearchResult] = []
        seen: set[str] = set()
        for anchor in await page.query_selector_all("a:has(h3)"):
            href = await anchor.get_attribute("href")
            heading = await anchor.query_selector("h3")
            title = (await heading.inner_text()).strip() if heading is not None else ""
            if not href or not title:
                continue
            if href.startswith("/url?q="):
                href = href[len("/url?q=") :].split("&", 1)[0]
            host = hostname_of(href) or ""
            if not href.startswith("http") or "google." in host:
                continue
            if href in seen:
                continue
            seen.add(href)
            results.append(
                SearchResult(title=title, url=href, snippet="", source=hostname_of(href))
            )
            if len(results) >= options.max_results:
                break
        return results
