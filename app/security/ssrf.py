"""SSRF-защита для open_url: схема http/https, DNS-resolve, блок неглобальных IP."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse


class SSRFError(ValueError):
    """URL запрещён политикой SSRF (схема, приватный/неглобальный IP, DNS-ошибка)."""


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Resolver = Callable[[str], Awaitable[list[IPAddress]]]

_METADATA_IP = ipaddress.ip_address("169.254.169.254")  # cloud metadata endpoint


def _assert_ip_public(ip: IPAddress, hostname: str) -> None:
    """Блокирует всё неглобальное: loopback, private, link-local, multicast и т.д."""
    if ip == _METADATA_IP:
        raise SSRFError(f"{hostname}: cloud metadata address 169.254.169.254 blocked")
    if ip.is_multicast or ip.is_reserved or ip.is_unspecified or not ip.is_global:
        raise SSRFError(f"{hostname}: non-public IP {ip} blocked")


async def resolve_ips(hostname: str) -> list[IPAddress]:
    """DNS A/AAAA resolve хоста; IP-литерал возвращается как есть; ошибки → SSRFError."""
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError) as exc:
        raise SSRFError(f"cannot resolve {hostname!r}") from exc
    ips: set[IPAddress] = set()
    for info in infos:
        try:
            ips.add(ipaddress.ip_address(info[4][0]))
        except ValueError:
            continue
    if not ips:
        raise SSRFError(f"cannot resolve {hostname!r}")
    return sorted(ips, key=str)


async def validate_url_public_ips(
    url: str, *, resolver: Resolver | None = None
) -> tuple[str, list[IPAddress]]:
    """Проверить URL и вернуть (hostname, проверенные публичные IP) для pinned connect.

    Только http/https; каждый resolved IP глобальный. Порт нестандартный разрешён.
    Возвращённые IP — единственные разрешённые цели connect (anti DNS-rebinding):
    caller ОБЯЗАН подключаться к ним, а не перезапрашивать DNS.
    resolver инъектируется для тестов.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError(f"scheme {parsed.scheme!r} is not allowed")
    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("URL has no hostname")
    try:
        # IP-литерал проверяем напрямую, без resolver (важно для инъекции в тестах).
        ips = [ipaddress.ip_address(hostname)]
    except ValueError:
        resolve = resolver if resolver is not None else resolve_ips
        ips = await resolve(hostname)
    if not ips:
        raise SSRFError(f"cannot resolve {hostname!r}")
    for ip in ips:
        _assert_ip_public(ip, hostname)
    return hostname, ips


async def assert_url_public(url: str, *, resolver: Resolver | None = None) -> str:
    """Проверить URL: только http/https + каждый resolved IP глобальный. Возвращает url.

    Порт нестандартный разрешён; схема — строго. resolver инъектируется для тестов.
    """
    await validate_url_public_ips(url, resolver=resolver)
    return url
