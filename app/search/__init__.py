"""Web search + open_url (F1): бэкенды, менеджер fallback, SSRF-safe fetcher."""

from app.search.base import (
    BackendUnavailableError,
    SearchBackend,
    SearchBackendError,
    SearchOptions,
    SearchOutcome,
    SearchResult,
    SupportsAIOverview,
)
from app.search.brave import BraveSearchBackend
from app.search.fetcher import MAX_BYTES, FetchedPage, fetch_url
from app.search.jina import JinaReaderBackend, JinaSearchBackend
from app.search.manager import SearchManager
from app.search.playwright_google import PlaywrightGoogleBackend
from app.search.reader import html_to_text
from app.search.serpapi_ai_overview import SerpApiAIOverviewBackend
from app.search.serper import SerperBackend

__all__ = [
    "MAX_BYTES",
    "BackendUnavailableError",
    "BraveSearchBackend",
    "FetchedPage",
    "JinaReaderBackend",
    "JinaSearchBackend",
    "PlaywrightGoogleBackend",
    "SearchBackend",
    "SearchBackendError",
    "SearchManager",
    "SearchOptions",
    "SearchOutcome",
    "SearchResult",
    "SerpApiAIOverviewBackend",
    "SerperBackend",
    "SupportsAIOverview",
    "fetch_url",
    "html_to_text",
]
