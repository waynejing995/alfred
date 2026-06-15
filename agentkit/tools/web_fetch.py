from __future__ import annotations

import ipaddress
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

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return

    if _is_blocked_address(address):
        raise ValueError(f"web_fetch blocked internal address: {address}")


def _is_blocked_address(address: ipaddress._BaseAddress) -> bool:
    blocked_networks = [
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.169.254/32"),
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("::1/128"),
    ]
    return any(address in network for network in blocked_networks)
