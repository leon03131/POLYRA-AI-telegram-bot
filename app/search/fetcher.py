"""open_url pipeline: SSRF-safe fetch, ручные редиректы, лимит байт, извлечение текста.

Порядок: assert_url_public → ручные редиректы (каждый Location проверяется заново,
redirect-into-private блокируется) → проверка content-type по заголовку до чтения →
stream read не более max_bytes → текст (Jina reader или локальный html_to_text).
"""

from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from app.search.base import SearchBackendError
from app.search.jina import JinaReaderBackend
from app.search.reader import html_to_text
from app.security.ssrf import Resolver, assert_url_public

MAX_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT = ("text/html", "text/plain", "application/xhtml")

_USER_AGENT = "Mozilla/5.0 (compatible; aibot-open-url/1.0)"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHUNK_SIZE = 65536


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """Результат open_url: финальный URL, media-type, текст, флаг обрезки."""

    url: str
    content_type: str
    text: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class _RawFetch:
    """Сырое тело ответа после редиректов и лимита байт."""

    final_url: str
    media_type: str
    content_type_header: str
    body: bytes
    truncated: bool


def _parse_charset(content_type_header: str) -> str:
    """charset из content-type заголовка; по умолчанию utf-8."""
    for param in content_type_header.split(";")[1:]:
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset" and value.strip():
            return value.strip().strip('"')
    return "utf-8"


def _validate_response(response: httpx.Response) -> str:
    """Проверить status/content-type (до чтения тела); вернуть media_type."""
    if response.status_code >= 400:
        raise SearchBackendError(
            f"open_url: HTTP {response.status_code}",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    content_type_header: str = response.headers.get("content-type", "")
    media_type = content_type_header.split(";")[0].strip().lower()
    if not any(media_type.startswith(allowed) for allowed in ALLOWED_CONTENT):
        raise SearchBackendError(f"open_url: unsupported content-type {media_type!r}")
    return media_type


async def _read_body(response: httpx.Response, max_bytes: int) -> tuple[bytes, bool]:
    """Stream read не более max_bytes; возвращает (body, truncated)."""
    body = bytearray()
    truncated = False
    async for chunk in response.aiter_bytes(_CHUNK_SIZE):
        body.extend(chunk)
        if len(body) > max_bytes:
            del body[max_bytes:]
            truncated = True
            break
    return bytes(body), truncated


async def _fetch_raw(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    max_redirects: int,
    resolver: Resolver | None,
) -> _RawFetch:
    """GET с ручными редиректами; каждый Location проходит assert_url_public заново."""
    current = url
    redirects = 0
    while True:
        try:
            async with client.stream(
                "GET", current, headers={"User-Agent": _USER_AGENT}
            ) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    redirects += 1
                    if redirects > max_redirects:
                        raise SearchBackendError("open_url: too many redirects")
                    location = response.headers.get("location")
                    if not location:
                        raise SearchBackendError("open_url: redirect without Location")
                    current = urljoin(current, location)
                    # redirect-into-private блок: каждый хоп проверяем заново
                    await assert_url_public(current, resolver=resolver)
                    continue
                media_type = _validate_response(response)
                body, truncated = await _read_body(response, max_bytes)
                return _RawFetch(
                    final_url=str(response.url),
                    media_type=media_type,
                    content_type_header=response.headers.get("content-type", ""),
                    body=body,
                    truncated=truncated,
                )
        except httpx.TimeoutException as exc:
            raise SearchBackendError("open_url: timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise SearchBackendError(f"open_url: network error ({exc.__class__.__name__})") from exc


async def fetch_url(
    url: str,
    *,
    max_bytes: int = MAX_BYTES,
    timeout: float = 20.0,  # noqa: ASYNC109  # per-request httpx timeout — контракт F1
    max_redirects: int = 5,
    reader: JinaReaderBackend | None = None,
    resolver: Resolver | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> FetchedPage:
    """GET url с SSRF-проверками (включая каждый редирект) и лимитом тела.

    SSRFError пробрасывается как есть; httpx-ошибки → SearchBackendError.
    resolver/http_client — точки инъекции для тестов.
    """
    await assert_url_public(url, resolver=resolver)
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(connect=5.0, read=timeout, write=timeout, pool=timeout),
    )
    try:
        raw = await _fetch_raw(
            client, url, max_bytes=max_bytes, max_redirects=max_redirects, resolver=resolver
        )
    finally:
        if owns_client:
            await client.aclose()

    decoded = raw.body.decode(_parse_charset(raw.content_type_header), errors="replace")
    if raw.media_type == "text/plain":
        text = decoded
    else:
        extracted = await reader.read(raw.final_url) if reader is not None else None
        text = extracted if extracted is not None else html_to_text(decoded)
    return FetchedPage(
        url=raw.final_url, content_type=raw.media_type, text=text, truncated=raw.truncated
    )
