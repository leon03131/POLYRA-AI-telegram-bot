"""Unit-тесты SSRF-политики (app.security.ssrf)."""

import asyncio
import ipaddress
import socket

import pytest

from app.security import ssrf
from app.security.ssrf import SSRFError, assert_url_public, resolve_ips, validate_url_public_ips

_PUBLIC = [ipaddress.ip_address("93.184.216.34")]
_PRIVATE = [ipaddress.ip_address("10.1.2.3")]


def _resolver(ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address]):
    async def resolve(hostname: str):
        return ips

    return resolve


async def test_http_and_https_allowed() -> None:
    url = "http://example.com/page"
    assert await assert_url_public(url, resolver=_resolver(_PUBLIC)) == url
    secure = "https://example.com:8443/x?y=1"  # нестандартный порт разрешён
    assert await assert_url_public(secure, resolver=_resolver(_PUBLIC)) == secure


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/x", "gopher://example.com/1"],
)
async def test_non_http_schemes_blocked(url: str) -> None:
    with pytest.raises(SSRFError):
        await assert_url_public(url, resolver=_resolver(_PUBLIC))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.1.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://0.0.0.0/",
        "http://224.0.0.1/",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[fc00::1]/",
    ],
)
async def test_ip_literals_blocked(url: str) -> None:
    # IP-литералы: DNS не нужен, блок детерминирован.
    with pytest.raises(SSRFError):
        await assert_url_public(url)


async def test_hostname_resolving_to_private_ip_blocked() -> None:
    with pytest.raises(SSRFError):
        await assert_url_public("http://internal.example", resolver=_resolver(_PRIVATE))


async def test_module_resolve_ips_used_when_no_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_resolve(hostname: str):
        return _PRIVATE

    monkeypatch.setattr(ssrf, "resolve_ips", fake_resolve)
    with pytest.raises(SSRFError):
        await assert_url_public("http://example.com")


async def test_dns_failure_raises_ssrf_error(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(*args: object, **kwargs: object) -> list[object]:
        raise socket.gaierror("no dns")

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(SSRFError):
        await resolve_ips("unresolvable.example")


async def test_resolve_ips_ip_literal_passthrough() -> None:
    ips = await resolve_ips("93.184.216.34")
    assert ips == [ipaddress.ip_address("93.184.216.34")]


async def test_missing_hostname_blocked() -> None:
    with pytest.raises(SSRFError):
        await assert_url_public("http://", resolver=_resolver(_PUBLIC))


# --- validate_url_public_ips (pinned-connect контракт A19) ----------------------


async def test_validate_returns_hostname_and_ips() -> None:
    hostname, ips = await validate_url_public_ips(
        "https://example.com:8443/x", resolver=_resolver(_PUBLIC)
    )
    assert hostname == "example.com"
    assert ips == _PUBLIC


async def test_validate_ip_literal_passthrough_no_resolver() -> None:
    async def fail_resolver(hostname: str):
        raise AssertionError("resolver must not be called for IP literals")

    hostname, ips = await validate_url_public_ips(
        "http://93.184.216.34/path", resolver=fail_resolver
    )
    assert hostname == "93.184.216.34"
    assert ips == _PUBLIC


async def test_validate_private_ip_literal_blocked() -> None:
    with pytest.raises(SSRFError):
        await validate_url_public_ips("http://10.0.0.1/", resolver=_resolver(_PUBLIC))


async def test_validate_hostname_resolving_to_private_blocked() -> None:
    with pytest.raises(SSRFError):
        await validate_url_public_ips("http://internal.example", resolver=_resolver(_PRIVATE))


async def test_validate_bad_scheme_blocked() -> None:
    with pytest.raises(SSRFError):
        await validate_url_public_ips("file:///etc/passwd", resolver=_resolver(_PUBLIC))
