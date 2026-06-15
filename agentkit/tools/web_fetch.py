from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx


def web_fetch(url: str, timeout: float = 10.0) -> dict[str, str | int]:
    _assert_public_url(url)
    response = httpx.get(url, timeout=timeout, follow_redirects=False)
    return {
        "url": str(response.url),
        "status_code": response.status_code,
        "content": response.text,
        "trust": "untrusted",
    }


def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"unsupported web_fetch scheme: {parsed.scheme}")
    if not parsed.hostname:
        raise ValueError("web_fetch URL must include a host")

    host = parsed.hostname.strip("[]").lower()
    if host == "localhost":
        raise ValueError("web_fetch blocked internal host: localhost")

    addresses = _resolve_addresses(host)
    if not addresses:
        raise ValueError(f"web_fetch could not resolve host: {host}")

    for address in addresses:
        if _is_blocked_address(address):
            raise ValueError(f"web_fetch blocked internal address: {address}")


def _is_blocked_address(address: ipaddress._BaseAddress) -> bool:
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address == ipaddress.ip_address("169.254.169.254")
    )


def _resolve_addresses(host: str) -> set[ipaddress._BaseAddress]:
    try:
        return {ipaddress.ip_address(host)}
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return {ipaddress.ip_address(info[4][0]) for info in infos}
