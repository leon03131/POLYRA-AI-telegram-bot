"""Unit-тесты поисковых бэкендов, SearchManager, reader и fetcher (F1)."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.search.manager as manager_module
from app.search.base import (
    BackendUnavailableError,
    SearchBackendError,
    SearchOptions,
    SearchOutcome,
    SearchResult,
)
from app.search.brave import BraveSearchBackend
from app.search.fetcher import PinnedHTTPTransport, fetch_url
from app.search.jina import JinaReaderBackend, JinaSearchBackend
from app.search.manager import SearchManager
from app.search.reader import html_to_text
from app.search.serpapi_ai_overview import SerpApiAIOverviewBackend
from app.search.serper import SerperBackend
from app.security.crypto import CryptoBox
from app.security.ssrf import SSRFError


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)


async def _public_resolver(hostname: str):
    return [ipaddress.ip_address("93.184.216.34")]


# ---------------------------------------------------------------- serper


async def test_serper_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://google.serper.dev/search"
        assert request.headers["X-API-KEY"] == "key-1"
        body = json.loads(request.content)
        assert body == {"q": "погода", "gl": "ru", "hl": "ru", "num": 5}
        return httpx.Response(
            200,
            json={
                "organic": [
                    {
                        "title": "T1",
                        "link": "https://a.example/x",
                        "snippet": "S1",
                        "date": "2 days ago",
                    },
                    {"title": "T2", "link": "https://b.example/y", "snippet": "S2"},
                ]
            },
        )

    backend = SerperBackend(api_key="key-1", http_client=_client(handler))
    results = await backend.search("погода", SearchOptions())
    assert [r.url for r in results] == ["https://a.example/x", "https://b.example/y"]
    assert results[0].published_at == "2 days ago"
    assert results[0].source == "a.example"
    assert results[1].published_at is None


@pytest.mark.parametrize("status", [401, 403])
async def test_serper_auth_error_unavailable(status: int) -> None:
    backend = SerperBackend(
        api_key="key-1", http_client=_client(lambda req: httpx.Response(status))
    )
    with pytest.raises(BackendUnavailableError):
        await backend.search("q", SearchOptions())


async def test_serper_500_retryable_single_retry() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": "boom"})

    backend = SerperBackend(api_key="k", http_client=_client(handler))
    with pytest.raises(SearchBackendError) as exc_info:
        await backend.search("q", SearchOptions())
    assert exc_info.value.retryable is True
    assert calls == 2  # 1 исходная попытка + 1 повтор


async def test_serper_invalid_json() -> None:
    backend = SerperBackend(
        api_key="k", http_client=_client(lambda req: httpx.Response(200, content=b"not json"))
    )
    with pytest.raises(SearchBackendError):
        await backend.search("q", SearchOptions())


async def test_serper_unconfigured() -> None:
    backend = SerperBackend(api_key=None, http_client=_client(lambda req: httpx.Response(200)))
    assert backend.is_configured() is False
    with pytest.raises(BackendUnavailableError):
        await backend.search("q", SearchOptions())


# ---------------------------------------------------------------- brave


async def test_brave_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Subscription-Token"] == "brave-key"
        assert request.url.params["q"] == "hello"
        assert request.url.params["count"] == "5"
        assert request.url.params["country"] == "ru"
        assert request.url.params["search_lang"] == "ru"
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "T",
                            "url": "https://ex.com/a",
                            "description": "Hello <strong>world</strong>",
                            "page_age": "2026-09-01T00:00:00Z",
                            "profile": {"name": "Ex"},
                        },
                        {"title": "T2", "url": "https://ex.org/b", "description": "Plain"},
                    ]
                }
            },
        )

    backend = BraveSearchBackend(api_key="brave-key", http_client=_client(handler))
    results = await backend.search("hello", SearchOptions())
    assert results[0].snippet == "Hello world"
    assert results[0].published_at == "2026-09-01T00:00:00Z"
    assert results[0].source == "Ex"
    assert results[1].source == "ex.org"
    assert results[1].published_at is None


# ---------------------------------------------------------------- serpapi


def _serpapi_handler(
    step1: dict[str, Any], step2: dict[str, Any] | None = None
) -> tuple[Callable[[httpx.Request], httpx.Response], list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.params.get("engine") == "google_ai_overview":
            assert step2 is not None, "unexpected second request"
            return httpx.Response(200, json=step2)
        return httpx.Response(200, json=step1)

    return handler, calls


async def test_serpapi_ai_overview_text_blocks() -> None:
    step1 = {
        "ai_overview": {
            "text_blocks": [
                {"type": "paragraph", "snippet": "Ответ на вопрос."},
                {"type": "heading", "snippet": "Подробности"},
            ],
            "references": [
                {
                    "title": "Источник",
                    "link": "https://src.example/a",
                    "snippet": "Ref snippet",
                    "source": "Src",
                }
            ],
        }
    }
    handler, calls = _serpapi_handler(step1)
    backend = SerpApiAIOverviewBackend(api_key="serp-key", http_client=_client(handler))
    outcome = await backend.ai_overview("почему небо синее", SearchOptions(mode="ai_overview"))
    assert outcome.backend == "serpapi_aio"
    assert outcome.ai_overview_text == "Ответ на вопрос.\nПодробности"
    assert [r.url for r in outcome.results] == ["https://src.example/a"]
    assert outcome.results[0].source == "Src"
    assert len(calls) == 1
    assert calls[0].url.params["engine"] == "google"


async def test_serpapi_ai_overview_page_token_two_steps() -> None:
    step1 = {"ai_overview": {"page_token": "tok-1"}}
    step2 = {
        "ai_overview": {
            "text_blocks": [{"type": "paragraph", "snippet": "Overview text"}],
            "references": [{"title": "R", "link": "https://r.example/", "snippet": "s"}],
        }
    }
    handler, calls = _serpapi_handler(step1, step2)
    backend = SerpApiAIOverviewBackend(api_key="serp-key", http_client=_client(handler))
    outcome = await backend.ai_overview("what is X", SearchOptions(mode="ai_overview"))
    assert outcome.ai_overview_text == "Overview text"
    assert [r.url for r in outcome.results] == ["https://r.example/"]
    assert len(calls) == 2
    assert calls[1].url.params["engine"] == "google_ai_overview"
    assert calls[1].url.params["page_token"] == "tok-1"


async def test_serpapi_ai_overview_error_degrades_to_organic() -> None:
    step1 = {
        "ai_overview": {"error": "Couldn't generate AI overview"},
        "organic_results": [
            {"title": "O", "link": "https://o.example/", "snippet": "os", "date": "2026-09-01"}
        ],
    }
    handler, calls = _serpapi_handler(step1)
    backend = SerpApiAIOverviewBackend(api_key="serp-key", http_client=_client(handler))
    outcome = await backend.ai_overview("q", SearchOptions(mode="ai_overview"))
    assert outcome.ai_overview_text is None
    assert [r.url for r in outcome.results] == ["https://o.example/"]
    assert outcome.results[0].published_at == "2026-09-01"
    assert len(calls) == 1


async def test_serpapi_normal_search() -> None:
    step1 = {"organic_results": [{"title": "O", "link": "https://o.example/p", "snippet": "os"}]}
    handler, calls = _serpapi_handler(step1)
    backend = SerpApiAIOverviewBackend(api_key="serp-key", http_client=_client(handler))
    results = await backend.search("q", SearchOptions())
    assert [r.url for r in results] == ["https://o.example/p"]
    assert results[0].source == "o.example"
    assert calls[0].url.params["engine"] == "google"


# ---------------------------------------------------------------- jina


async def test_jina_search_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://s.jina.ai/")
        assert request.headers["Authorization"] == "Bearer jina-key"
        assert request.headers["Accept"] == "application/json"
        assert request.url.params["q"] == "новости"
        return httpx.Response(
            200,
            json=[
                {
                    "url": "https://n.example/1",
                    "title": "N1",
                    "content": "x" * 400,
                    "timestamp": "2026-09-18",
                }
            ],
        )

    backend = JinaSearchBackend(api_key="jina-key", http_client=_client(handler))
    results = await backend.search("новости", SearchOptions())
    assert results[0].snippet == "x" * 300
    assert results[0].published_at == "2026-09-18"
    assert results[0].source == "n.example"


async def test_jina_search_requires_key() -> None:
    backend = JinaSearchBackend(api_key=None, http_client=_client(lambda req: httpx.Response(200)))
    assert backend.is_configured() is False
    with pytest.raises(BackendUnavailableError):
        await backend.search("q", SearchOptions())


async def test_jina_reader_read() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://r.jina.ai/http://example.com/")
        assert request.headers["Accept"] == "application/json"
        assert request.headers["X-Timeout"] == "30"
        return httpx.Response(200, json={"data": {"content": "page content"}})

    reader = JinaReaderBackend(api_key=None, http_client=_client(handler))
    assert await reader.read("http://example.com/") == "page content"


async def test_jina_reader_error_returns_none() -> None:
    reader = JinaReaderBackend(http_client=_client(lambda req: httpx.Response(500)))
    assert await reader.read("http://example.com/") is None


# ---------------------------------------------------------------- manager


class _FakeBackend:
    """Минимальная реализация контракта SearchBackend для тестов менеджера."""

    def __init__(
        self,
        backend_id: str,
        *,
        configured: bool = True,
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.backend_id = backend_id
        self._configured = configured
        self._results = results or []
        self._error = error
        self.calls = 0

    def is_configured(self) -> bool:
        return self._configured

    async def search(self, query: str, options: SearchOptions) -> list[SearchResult]:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return list(self._results)


def _make_manager(backends: list) -> tuple[SearchManager, list]:
    """SearchManager с подменёнными _load_backends/_record_health (БД не нужна)."""
    health_log: list[tuple[str, bool, str | None]] = []

    async def _load(*, include_disabled: bool = False) -> list:
        return list(backends)

    async def _record(backend_id: str, ok: bool, error: str | None) -> None:
        health_log.append((backend_id, ok, error))

    manager = SearchManager(session_factory=None, crypto=CryptoBox("test-key"))
    manager._load_backends = _load
    manager._record_health = _record
    return manager, health_log


async def test_manager_fallback_order() -> None:
    first = _FakeBackend("serper", error=BackendUnavailableError("no key"))
    second = _FakeBackend(
        "brave", results=[SearchResult(title="t", url="https://x.example/", snippet="s")]
    )
    manager, health = _make_manager([first, second])
    outcome = await manager.search("q", SearchOptions())
    assert outcome.backend == "brave"
    assert [r.url for r in outcome.results] == ["https://x.example/"]
    assert ("serper", False, "no key") in health
    assert ("brave", True, None) in health


async def test_manager_skips_unconfigured() -> None:
    off = _FakeBackend("serper", configured=False)
    on = _FakeBackend(
        "jina_search", results=[SearchResult(title="t", url="https://y.example/", snippet="")]
    )
    manager, _ = _make_manager([off, on])
    outcome = await manager.search("q", SearchOptions())
    assert off.calls == 0
    assert outcome.backend == "jina_search"


async def test_manager_all_failed() -> None:
    a = _FakeBackend("serper", error=SearchBackendError("boom", retryable=True))
    b = _FakeBackend("brave", error=BackendUnavailableError("down"))
    manager, _ = _make_manager([a, b])
    with pytest.raises(SearchBackendError, match="all backends failed"):
        await manager.search("q", SearchOptions())


async def test_manager_dedupe_and_max_results() -> None:
    results = [
        SearchResult(title="1", url="https://a.example/1", snippet=""),
        SearchResult(title="dup", url="https://a.example/1", snippet=""),
        SearchResult(title="2", url="https://a.example/2", snippet=""),
        SearchResult(title="3", url="https://a.example/3", snippet=""),
    ]
    manager, _ = _make_manager([_FakeBackend("serper", results=results)])
    outcome = await manager.search("q", SearchOptions(max_results=2))
    assert [r.url for r in outcome.results] == ["https://a.example/1", "https://a.example/2"]


class _FakeAIOBackend(_FakeBackend):
    async def ai_overview(self, query: str, options: SearchOptions) -> SearchOutcome:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return SearchOutcome(
            results=[SearchResult(title="r", url="https://r.example/", snippet="")],
            backend=self.backend_id,
            ai_overview_text="overview text",
        )


async def test_manager_auto_normal_first_even_for_question() -> None:
    """A32: auto НЕ идёт сразу в AIO даже для вопроса — сначала normal-цепочка."""
    aio = _FakeAIOBackend("serpapi_aio")
    normal = _FakeBackend(
        "serper", results=[SearchResult(title="n", url="https://n.example/", snippet="")]
    )
    manager, _ = _make_manager([aio, normal])
    outcome = await manager.search("почему небо синее?", SearchOptions(mode="auto"))
    assert outcome.backend == "serper"
    assert aio.calls == 0  # AIO не вызывался: normal дал результаты


async def test_manager_auto_empty_normal_falls_to_ai_overview() -> None:
    """A32: auto + normal вернул 0 результатов → AI Overview."""
    aio = _FakeAIOBackend("serpapi_aio")
    empty = _FakeBackend("serper", results=[])
    manager, _ = _make_manager([aio, empty])
    outcome = await manager.search("фото котиков", SearchOptions(mode="auto"))
    assert outcome.backend == "serpapi_aio"
    assert outcome.ai_overview_text == "overview text"
    assert empty.calls == 1 and aio.calls == 1


async def test_manager_auto_normal_error_falls_to_ai_overview() -> None:
    """A32: auto + normal упал (SearchBackendError) → AI Overview."""
    aio = _FakeAIOBackend("serpapi_aio")
    broken = _FakeBackend("serper", error=SearchBackendError("boom"))
    manager, _ = _make_manager([aio, broken])
    outcome = await manager.search("q", SearchOptions(mode="auto"))
    assert outcome.backend == "serpapi_aio"
    assert broken.calls == 1 and aio.calls == 1


async def test_manager_auto_both_failed_raises() -> None:
    """A32: auto + normal упал И AIO упал → SearchBackendError (не молчим)."""
    aio = _FakeAIOBackend("serpapi_aio", error=SearchBackendError("aio boom"))
    broken = _FakeBackend("serper", error=SearchBackendError("normal boom"))
    manager, _ = _make_manager([aio, broken])
    with pytest.raises(SearchBackendError, match="all backends failed"):
        await manager.search("q", SearchOptions(mode="auto"))


async def test_manager_auto_empty_normal_and_no_aio_returns_empty() -> None:
    """A32: auto + normal пуст + AIO недоступен → валидный пустой результат."""
    empty = _FakeBackend("serper", results=[])
    manager, _ = _make_manager([empty])
    outcome = await manager.search("q", SearchOptions(mode="auto"))
    assert outcome.backend == "serper"
    assert outcome.results == []


async def test_manager_ai_overview_without_references_falls_back() -> None:
    """A32: overview текст БЕЗ references не авторитетен → normal-цепочка."""

    class _NoRefsAIOBackend(_FakeBackend):
        async def ai_overview(self, query: str, options: SearchOptions) -> SearchOutcome:
            self.calls += 1
            return SearchOutcome(results=[], backend=self.backend_id, ai_overview_text="text")

    aio = _NoRefsAIOBackend("serpapi_aio")
    normal = _FakeBackend(
        "serper", results=[SearchResult(title="n", url="https://n.example/", snippet="")]
    )
    manager, _ = _make_manager([aio, normal])
    outcome = await manager.search("почему?", SearchOptions(mode="ai_overview"))
    assert outcome.backend == "serper"
    assert outcome.ai_overview_text is None


async def test_manager_ai_overview_failure_falls_back_to_normal() -> None:
    aio = _FakeAIOBackend("serpapi_aio", error=SearchBackendError("aio boom"))
    normal = _FakeBackend(
        "serper", results=[SearchResult(title="n", url="https://n.example/", snippet="")]
    )
    manager, _ = _make_manager([aio, normal])
    outcome = await manager.search("почему?", SearchOptions(mode="ai_overview"))
    assert outcome.backend == "serper"


# ---------------------------------------------------------------- reader


def test_html_to_text_strips_scripts_and_collapses() -> None:
    html = """
    <html><head><style>body { color: red; }</style><script>var x = 1;</script></head>
    <body>
      <nav>menu</nav>
      <h1>Заголовок</h1>
      <p>Первый <b>абзац</b> текста.</p>
      <script>alert(1)</script>
      <p>Второй абзац</p>
    </body></html>
    """
    text = html_to_text(html)
    assert "var x" not in text
    assert "alert" not in text
    assert "color: red" not in text
    assert "menu" not in text
    assert "Заголовок" in text
    assert "Первый абзац текста." in text
    assert "Второй абзац" in text
    assert "\n\n\n" not in text


def test_html_to_text_max_chars() -> None:
    assert html_to_text("<p>" + "a" * 100 + "</p>", max_chars=10) == "a" * 10


# ---------------------------------------------------------------- fetcher


async def test_fetch_plain_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"hello world", headers={"content-type": "text/plain; charset=utf-8"}
        )

    page = await fetch_url(
        "http://example.com/note.txt", resolver=_public_resolver, http_client=_client(handler)
    )
    assert page.text == "hello world"
    assert page.content_type == "text/plain"
    assert page.truncated is False
    assert page.url == "http://example.com/note.txt"


async def test_fetch_html_extracts_text() -> None:
    html = b"<html><body><script>x()</script><p>Content <b>here</b></p></body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=html, headers={"content-type": "text/html; charset=utf-8"}
        )

    page = await fetch_url(
        "http://example.com/", resolver=_public_resolver, http_client=_client(handler)
    )
    assert page.content_type == "text/html"
    assert "Content here" in page.text
    assert "x()" not in page.text


async def test_fetch_redirect_to_public_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(
            200, content=b"<p>After redirect</p>", headers={"content-type": "text/html"}
        )

    page = await fetch_url(
        "http://example.com/start", resolver=_public_resolver, http_client=_client(handler)
    )
    assert page.url == "http://example.com/final"
    assert "After redirect" in page.text


async def test_fetch_redirect_to_private_blocked() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": "http://127.0.0.1:8080/admin"})

    with pytest.raises(SSRFError):
        await fetch_url(
            "http://example.com/start", resolver=_public_resolver, http_client=_client(handler)
        )
    assert calls == 1  # второй HTTP-запрос не выполнялся


async def test_fetch_private_url_blocked_before_request() -> None:
    handler_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal handler_called
        handler_called = True
        return httpx.Response(200)

    with pytest.raises(SSRFError):
        await fetch_url(
            "http://169.254.169.254/latest/meta-data",
            resolver=_public_resolver,
            http_client=_client(handler),
        )
    assert handler_called is False


async def test_fetch_pdf_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})

    with pytest.raises(SearchBackendError, match="content-type"):
        await fetch_url(
            "http://example.com/f.pdf", resolver=_public_resolver, http_client=_client(handler)
        )


async def test_fetch_truncated_over_max_bytes() -> None:
    payload = b"x" * 5000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "text/plain"})

    page = await fetch_url(
        "http://example.com/big.txt",
        max_bytes=1000,
        resolver=_public_resolver,
        http_client=_client(handler),
    )
    assert page.truncated is True
    assert len(page.text) == 1000


class _FakeReader:
    def __init__(self, text: str | None) -> None:
        self._text = text
        self.calls: list[str] = []

    async def read(
        self,
        url: str,
        *,
        timeout: float = 30.0,  # noqa: ASYNC109  # зеркалит JinaReaderBackend.read
        max_chars: int = 8000,
    ):
        self.calls.append(url)
        return self._text


async def test_fetch_uses_reader_when_provided() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html><p>raw</p></html>", headers={"content-type": "text/html"}
        )

    reader = _FakeReader("reader text")
    page = await fetch_url(
        "http://example.com/",
        resolver=_public_resolver,
        http_client=_client(handler),
        reader=reader,
    )
    assert page.text == "reader text"
    assert reader.calls == ["http://example.com/"]


async def test_fetch_reader_none_falls_back_to_local_extraction() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html><p>local text</p></html>", headers={"content-type": "text/html"}
        )

    reader = _FakeReader(None)
    page = await fetch_url(
        "http://example.com/",
        resolver=_public_resolver,
        http_client=_client(handler),
        reader=reader,
    )
    assert "local text" in page.text


async def test_fetch_reader_exception_falls_back_to_local_extraction() -> None:
    """A31: reader упал с исключением → локальная экстракция уже скачанного тела."""

    class _BoomReader:
        async def read(self, url: str, **kwargs: Any) -> str | None:
            raise RuntimeError("jina down")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html><p>local text</p></html>", headers={"content-type": "text/html"}
        )

    page = await fetch_url(
        "http://example.com/",
        resolver=_public_resolver,
        http_client=_client(handler),
        reader=_BoomReader(),
    )
    assert "local text" in page.text


# ------------------------------------------------------- pinned connect (A19)


def _record_transport_sends(
    monkeypatch: pytest.MonkeyPatch, body: bytes = b"<html><p>pinned</p></html>"
) -> list[httpx.Request]:
    """Перехватить запросы, уходящие в реальный транспорт (без сети)."""
    sent: list[httpx.Request] = []

    async def fake_send(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200, content=body, headers={"content-type": "text/html"}, request=request
        )

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_send)
    return sent


async def test_pinned_transport_rewrites_connect_target() -> None:
    """_pin_request: host → pinned IP, Host сохраняет реальный хост (+порт), SNI по хосту."""
    transport = PinnedHTTPTransport(pinned_ip="93.184.216.34", real_host="example.com")
    request = httpx.Request("GET", "https://example.com:8443/x")
    transport._pin_request(request)
    assert request.url.host == "93.184.216.34"
    assert request.url.port == 8443
    assert request.headers["Host"] == "example.com:8443"
    assert request.extensions["sni_hostname"] == "example.com"
    await transport.aclose()

    transport2 = PinnedHTTPTransport(pinned_ip="93.184.216.34", real_host="example.com")
    request2 = httpx.Request("GET", "http://example.com/x")
    transport2._pin_request(request2)
    assert request2.headers["Host"] == "example.com"  # дефолтный порт не добавляем
    assert "sni_hostname" not in request2.extensions  # только https
    await transport2.aclose()


async def test_fetch_pinned_connect_uses_validated_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS-rebinding/TOCTOU: после validation DNS «сменился» — connect всё равно
    идёт на СТАРЫЙ проверенный IP (запись транспорта), Host/SNI — реальный хост."""
    sent = _record_transport_sends(monkeypatch)
    resolves = 0

    async def rebinding_resolver(hostname: str):
        nonlocal resolves
        resolves += 1
        # validation-ответ — публичный IP; «повторный» ответ был бы приватным,
        # но pinned-путь DNS при connect НЕ перезапрашивает вовсе.
        return [ipaddress.ip_address("93.184.216.34")]

    page = await fetch_url("https://example.com/page", resolver=rebinding_resolver)
    assert "pinned" in page.text
    assert page.url == "https://example.com/page"  # реальный хост, не IP
    assert resolves == 1  # DNS только на валидации, не на connect
    assert len(sent) == 1
    request = sent[0]
    assert request.url.host == "93.184.216.34"  # connect-цель — проверенный IP
    assert request.headers["Host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com"  # TLS verify по хосту


async def test_fetch_pinned_ip_literal_same_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """IP-литерал в URL: тот же pinned-путь (host == ip), resolver не вызывается."""
    sent = _record_transport_sends(monkeypatch, body=b"plain")

    async def fail_resolver(hostname: str):
        raise AssertionError("resolver must not be called for IP literals")

    page = await fetch_url("http://93.184.216.34/note.txt", resolver=fail_resolver)
    assert page.text == "plain"
    assert page.url == "http://93.184.216.34/note.txt"
    assert sent[0].url.host == "93.184.216.34"
    assert sent[0].headers["Host"] == "93.184.216.34"


async def test_fetch_pinned_per_redirect_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый hop редиректа: своя валидация + pinned connect к IP нового хоста."""
    bodies = iter(
        [
            httpx.Response(
                302,
                headers={"location": "https://other.example/final"},
                request=None,  # выставим ниже
            ),
            httpx.Response(200, content=b"done", headers={"content-type": "text/plain"}),
        ]
    )
    sent: list[httpx.Request] = []

    async def fake_send(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
        sent.append(request)
        response = next(bodies)
        response.request = request
        return response

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_send)
    ips = {
        "example.com": ipaddress.ip_address("93.184.216.34"),
        "other.example": ipaddress.ip_address("93.184.216.35"),
    }

    async def resolver(hostname: str):
        return [ips[hostname]]

    page = await fetch_url("http://example.com/start", resolver=resolver)
    assert page.url == "https://other.example/final"
    assert page.text == "done"
    assert [r.url.host for r in sent] == ["93.184.216.34", "93.184.216.35"]
    assert sent[1].headers["Host"] == "other.example"
    assert sent[1].extensions["sni_hostname"] == "other.example"


# ------------------------------------------- malformed fixtures (A33)


async def test_serper_malformed_payload_list() -> None:
    backend = SerperBackend(
        api_key="k", http_client=_client(lambda req: httpx.Response(200, json=[1, 2]))
    )
    with pytest.raises(SearchBackendError, match="not an object"):
        await backend.search("q", SearchOptions())


async def test_serper_malformed_organic_not_list() -> None:
    backend = SerperBackend(
        api_key="k",
        http_client=_client(lambda req: httpx.Response(200, json={"organic": {"x": 1}})),
    )
    with pytest.raises(SearchBackendError, match="organic"):
        await backend.search("q", SearchOptions())


async def test_serper_skips_malformed_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "organic": [
                    "not-a-dict",
                    {"link": 123},
                    {"title": None, "link": "https://a.example/", "snippet": None},
                ]
            },
        )

    backend = SerperBackend(api_key="k", http_client=_client(handler))
    results = await backend.search("q", SearchOptions())
    assert len(results) == 1
    assert results[0].title == "" and results[0].snippet == ""


async def test_brave_malformed_web_not_dict() -> None:
    backend = BraveSearchBackend(
        api_key="k", http_client=_client(lambda req: httpx.Response(200, json={"web": []}))
    )
    with pytest.raises(SearchBackendError, match="web"):
        await backend.search("q", SearchOptions())


async def test_brave_malformed_results_not_list() -> None:
    backend = BraveSearchBackend(
        api_key="k",
        http_client=_client(lambda req: httpx.Response(200, json={"web": {"results": {}}})),
    )
    with pytest.raises(SearchBackendError, match="results"):
        await backend.search("q", SearchOptions())


async def test_brave_null_fields_tolerated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": None,
                            "url": "https://a.example/",
                            "description": None,
                            "profile": None,
                            "page_age": None,
                        }
                    ]
                }
            },
        )

    backend = BraveSearchBackend(api_key="k", http_client=_client(handler))
    results = await backend.search("q", SearchOptions())
    assert len(results) == 1
    assert results[0].snippet == "" and results[0].source == "a.example"
    assert results[0].published_at is None


async def test_jina_malformed_payload_scalar() -> None:
    backend = JinaSearchBackend(
        api_key="k", http_client=_client(lambda req: httpx.Response(200, json="nope"))
    )
    with pytest.raises(SearchBackendError, match="payload"):
        await backend.search("q", SearchOptions())


async def test_jina_malformed_data_not_list() -> None:
    backend = JinaSearchBackend(
        api_key="k",
        http_client=_client(lambda req: httpx.Response(200, json={"data": {"x": 1}})),
    )
    with pytest.raises(SearchBackendError, match="data"):
        await backend.search("q", SearchOptions())


async def test_serpapi_malformed_organic_not_list() -> None:
    handler, _ = _serpapi_handler({"organic_results": {"x": 1}})
    backend = SerpApiAIOverviewBackend(api_key="k", http_client=_client(handler))
    with pytest.raises(SearchBackendError, match="organic_results"):
        await backend.search("q", SearchOptions())


async def test_serpapi_overview_without_references_degrades() -> None:
    """A32: overview текст без references → деградация в organic, текст не выдаём."""
    step1 = {
        "ai_overview": {"text_blocks": [{"type": "paragraph", "snippet": "Орфанный обзор"}]},
        "organic_results": [{"title": "O", "link": "https://o.example/", "snippet": "os"}],
    }
    handler, _ = _serpapi_handler(step1)
    backend = SerpApiAIOverviewBackend(api_key="k", http_client=_client(handler))
    outcome = await backend.ai_overview("q", SearchOptions(mode="ai_overview"))
    assert outcome.ai_overview_text is None
    assert [r.url for r in outcome.results] == ["https://o.example/"]


# ------------------------------------------- lifecycle клиентов (A30)


class _FakeSession:
    async def commit(self) -> None:
        pass


class _FakeSessionCM:
    async def __aenter__(self) -> _FakeSession:
        return _FakeSession()

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _FakeConfigRepo:
    """Подмена SearchConfigRepository (класс-атрибут configs задаётся в тесте)."""

    configs: list[Any] = []

    def __init__(self, session: Any) -> None:
        pass

    async def list_all(self) -> list[Any]:
        return sorted(self.configs, key=lambda c: c.priority)

    async def get(self, backend_id: str) -> Any:
        return next((c for c in self.configs if c.backend_id == backend_id), None)

    async def set_health(self, backend_id: str, status: str, error: str | None = None) -> None:
        return None


class _CountingBackend:
    """Фабричный фейк бэкенда: считает инстансы/search/aclose."""

    backend_id = "serper"

    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key
        self.searches = 0
        self.closes = 0

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, options: SearchOptions) -> list[SearchResult]:
        self.searches += 1
        return [SearchResult(title="t", url="https://x.example/", snippet="s")]

    async def aclose(self) -> None:
        self.closes += 1


def _lifecycle_manager(
    monkeypatch: pytest.MonkeyPatch, configs: list[Any]
) -> tuple[SearchManager, list[_CountingBackend], CryptoBox]:
    """SearchManager с фейк-репозиторием и подсчётом созданных бэкендов."""
    crypto = CryptoBox("test-key")
    _FakeConfigRepo.configs = configs
    created: list[_CountingBackend] = []

    def factory(api_key: str | None) -> _CountingBackend:
        backend = _CountingBackend(api_key)
        created.append(backend)
        return backend

    monkeypatch.setattr(manager_module, "SearchConfigRepository", _FakeConfigRepo)
    monkeypatch.setitem(manager_module._BACKEND_FACTORIES, "serper", factory)
    manager = SearchManager(
        session_factory=lambda: cast(AbstractAsyncContextManager[AsyncSession], _FakeSessionCM()),
        crypto=crypto,
    )
    return manager, created, crypto


def _config(backend_id: str, crypto: CryptoBox, key: str | None, *, enabled: bool = True):
    return SimpleNamespace(
        backend_id=backend_id,
        enabled=enabled,
        priority=1,
        encrypted_api_key=crypto.encrypt(key) if key else None,
    )


async def test_manager_reuses_single_backend_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, created, crypto = _lifecycle_manager(
        monkeypatch, [_config("serper", CryptoBox("test-key"), "k1")]
    )
    await manager.search("a", SearchOptions())
    await manager.search("b", SearchOptions())
    assert len(created) == 1  # один клиент на многократные вызовы
    assert created[0].searches == 2
    await manager.aclose()
    assert created[0].closes == 1
    await manager.aclose()  # идемпотентно
    assert created[0].closes == 1


async def test_manager_recreates_backend_on_key_change(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config("serper", CryptoBox("test-key"), "k1")
    manager, created, crypto = _lifecycle_manager(monkeypatch, [config])
    await manager.search("a", SearchOptions())
    assert len(created) == 1
    assert created[0].api_key == "k1"
    config.encrypted_api_key = crypto.encrypt("k2")  # set_key через админку
    await manager.search("b", SearchOptions())
    assert len(created) == 2
    assert created[0].closes == 1  # старый закрыт при инвалидации
    assert created[1].api_key == "k2"
    await manager.aclose()
    assert created[1].closes == 1


async def test_get_reader_from_jina_config(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config("jina_search", CryptoBox("test-key"), "jina-key")
    manager, _, _ = _lifecycle_manager(monkeypatch, [config])
    reader = await manager.get_reader()
    assert isinstance(reader, JinaReaderBackend)
    assert reader.is_configured()
    assert await manager.get_reader() is reader  # кэш, не пересоздаётся
    await manager.aclose()


async def test_get_reader_none_when_disabled_or_keyless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crypto = CryptoBox("test-key")
    manager, _, _ = _lifecycle_manager(
        monkeypatch, [_config("jina_search", crypto, "jina-key", enabled=False)]
    )
    assert await manager.get_reader() is None
    manager2, _, _ = _lifecycle_manager(monkeypatch, [_config("jina_search", crypto, None)])
    assert await manager2.get_reader() is None
    await manager.aclose()
    await manager2.aclose()


async def test_get_reader_recreates_on_key_change(monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config("jina_search", CryptoBox("test-key"), "jina-key-1")
    manager, _, crypto = _lifecycle_manager(monkeypatch, [config])
    reader1 = await manager.get_reader()
    assert reader1 is not None
    config.encrypted_api_key = crypto.encrypt("jina-key-2")
    reader2 = await manager.get_reader()
    assert reader2 is not None and reader2 is not reader1
    await manager.aclose()


# ------------------------------------------- playwright cooldown (A33)


async def test_playwright_cooldown_skips_backend() -> None:
    """BackendUnavailableError от playwright → 15-мин in-memory cooldown."""
    playwright = _FakeBackend(
        "playwright_google", error=BackendUnavailableError("captcha detected")
    )
    brave = _FakeBackend(
        "brave", results=[SearchResult(title="t", url="https://x.example/", snippet="")]
    )
    manager, _ = _make_manager([playwright, brave])
    outcome1 = await manager.search("q1", SearchOptions())
    assert outcome1.backend == "brave"
    assert playwright.calls == 1
    outcome2 = await manager.search("q2", SearchOptions())
    assert outcome2.backend == "brave"
    assert playwright.calls == 1  # cooldown: повторно не дёргали


async def test_manager_429_falls_to_next_backend() -> None:
    """429 (retryable SearchBackendError) первого бэкенда → следующий по цепочке."""
    first = _FakeBackend("serper", error=SearchBackendError("serper: HTTP 429", retryable=True))
    second = _FakeBackend(
        "brave", results=[SearchResult(title="t", url="https://x.example/", snippet="")]
    )
    manager, health = _make_manager([first, second])
    outcome = await manager.search("q", SearchOptions())
    assert outcome.backend == "brave"
    assert ("serper", False, "serper: HTTP 429") in health
