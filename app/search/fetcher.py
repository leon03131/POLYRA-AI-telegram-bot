"""open_url pipeline: SSRF-safe fetch (pinned connect), ручные редиректы, лимит байт.

Порядок на каждый hop (включая редиректы): validate_url_public_ips (схема + DNS +
публичность КАЖДОГО IP) → PinnedHTTPTransport: connect идёт на ПРОВЕРЕННЫЙ IP
(anti DNS-rebinding/TOCTOU — повторный DNS при connect не используется), Host и
TLS hostname verification (SNI + cert match) остаются по реальному хосту через
extension "sni_hostname" (httpcore >= 1.0 использует его и для SNI, и для
проверки сертификата). TLS verification НЕ отключается (verify не трогаем).
IP-литерал в URL идёт тем же pinned-путём (host == ip).

Затем: ручные редиректы (каждый Location проверяется заново, redirect-into-private
блокируется) → проверка content-type по заголовку до чтения → stream read не более
max_bytes → текст (Jina reader или локальный html_to_text).

Reader (Jina) — внешний сервис: fetch делает ОН, наши connect-SSRF-проверки на его
сторону не распространяются, но reader получает только URL, уже прошедший
validate_url_public_ips; при любой ошибке/пустом ответе reader'а — fallback на
локальную экстракцию уже скачанного (и проверенного) тела.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx

from app.search.base import SearchBackendError
from app.search.jina import JinaReaderBackend
from app.search.reader import html_to_text
from app.security.ssrf import Resolver, validate_url_public_ips

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
ALLOWED_CONTENT = ("text/html", "text/plain", "application/xhtml")

_USER_AGENT = "Mozilla/5.0 (compatible; aibot-open-url/1.0)"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHUNK_SIZE = 65536
_DEFAULT_PORTS = {"http": 80, "https": 443}


class PinnedHTTPTransport(httpx.AsyncHTTPTransport):
    """Транспорт pinned connect: TCP/TLS к pinned_ip, Host/SNI — по real_host.

    request.url.host подменяется на проверенный IP (connect-цель), заголовок Host
    сохраняет реальный хост (+ нестандартный порт), extension "sni_hostname"
    заставляет httpcore делать SNI и проверку сертификата по реальному хосту.
    """

    def __init__(self, *, pinned_ip: str, real_host: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pinned_ip = pinned_ip
        self.real_host = real_host

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._pin_request(request)
        return await super().handle_async_request(request)

    def _pin_request(self, request: httpx.Request) -> None:
        """Переписать connect-цель на pinned IP, сохранив Host и TLS hostname."""
        url = request.url
        port = url.port
        request.url = url.copy_with(host=self.pinned_ip)
        if port is None or port == _DEFAULT_PORTS.get(url.scheme):
            request.headers["Host"] = self.real_host
        else:
            request.headers["Host"] = f"{self.real_host}:{port}"
        if url.scheme == "https":
            # httpcore: server_hostname = sni_hostname or origin.host → и SNI,
            # и ssl match_hostname идут по реальному хосту. verify НЕ отключаем.
            request.extensions["sni_hostname"] = self.real_host


# (pinned_ip, real_host) -> transport; инъекция для тестов (запись connect-цели).
PinnedTransportFactory = Callable[[str, str], httpx.AsyncBaseTransport]


def _default_transport_factory(pinned_ip: str, real_host: str) -> httpx.AsyncBaseTransport:
    return PinnedHTTPTransport(pinned_ip=pinned_ip, real_host=real_host)


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


class _Redirect(Exception):
    """Внутренний сигнал редиректа: несёт Location (ещё НЕ проверенный)."""

    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


async def _single_hop(client: httpx.AsyncClient, url: str, *, max_bytes: int) -> _RawFetch:
    """Один GET без следования редиректам; редирект → _Redirect(Location)."""
    try:
        async with client.stream("GET", url, headers={"User-Agent": _USER_AGENT}) as response:
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise SearchBackendError("open_url: redirect without Location")
                raise _Redirect(location)
            media_type = _validate_response(response)
            body, truncated = await _read_body(response, max_bytes)
            return _RawFetch(
                final_url=url,  # реальный хост, а не pinned IP из request.url
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
    transport_factory: PinnedTransportFactory | None = None,
) -> FetchedPage:
    """GET url с SSRF-проверками (pinned connect на каждый hop) и лимитом тела.

    SSRFError пробрасывается как есть; httpx-ошибки → SearchBackendError.
    resolver/http_client/transport_factory — точки инъекции для тестов.
    """
    redirects_left = max_redirects
    current = url
    while True:
        try:
            raw = await _fetch_raw_single(
                current,
                max_bytes=max_bytes,
                timeout=timeout,
                resolver=resolver,
                http_client=http_client,
                transport_factory=transport_factory,
            )
            break
        except _Redirect as redirect:
            redirects_left -= 1
            if redirects_left < 0:
                raise SearchBackendError("open_url: too many redirects") from None
            current = urljoin(current, redirect.location)
            # redirect-into-private блок: следующий hop проверит заново
            await validate_url_public_ips(current, resolver=resolver)

    decoded = raw.body.decode(_parse_charset(raw.content_type_header), errors="replace")
    if raw.media_type == "text/plain":
        text = decoded
    else:
        text = await _extract_text(raw.final_url, decoded, reader)
    return FetchedPage(
        url=raw.final_url, content_type=raw.media_type, text=text, truncated=raw.truncated
    )


async def _fetch_raw_single(
    url: str,
    *,
    max_bytes: int,
    timeout: float,  # noqa: ASYNC109  # per-request httpx timeout — контракт F1
    resolver: Resolver | None,
    http_client: httpx.AsyncClient | None,
    transport_factory: PinnedTransportFactory | None,
) -> _RawFetch:
    """Один hop: validate_url_public_ips → pinned (или инъектированный) клиент."""
    hostname, ips = await validate_url_public_ips(url, resolver=resolver)
    if http_client is not None:
        return await _single_hop(http_client, url, max_bytes=max_bytes)
    factory = transport_factory or _default_transport_factory
    transport = factory(str(ips[0]), hostname)
    async with httpx.AsyncClient(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=httpx.Timeout(connect=5.0, read=timeout, write=timeout, pool=timeout),
    ) as hop_client:
        return await _single_hop(hop_client, url, max_bytes=max_bytes)


async def _extract_text(final_url: str, decoded: str, reader: JinaReaderBackend | None) -> str:
    """Текст страницы: reader (если задан и справился) → локальный html_to_text.

    Reader получает только SSRF-проверенный публичный URL; любая ошибка reader'а
    (включая исключение, не только None) → fallback на локальную экстракцию уже
    скачанного тела. SSRF-проверки не обходятся ни на одном пути.
    """
    if reader is not None:
        try:
            extracted = await reader.read(final_url)
        except Exception:
            logger.warning("open_url: reader failed for %s, local fallback", final_url)
            extracted = None
        if extracted is not None:
            return extracted
    return html_to_text(decoded)
