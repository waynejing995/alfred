import importlib

import pytest

web_fetch_module = importlib.import_module("agentkit.tools.web_fetch")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8888",
        "http://127.0.0.1:8888",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://172.31.255.255",
    ],
)
def test_web_fetch_blocks_internal_addresses_before_network(url, monkeypatch):
    def fail_get(*_args, **_kwargs):
        raise AssertionError("network should not be called for blocked SSRF targets")

    monkeypatch.setattr(web_fetch_module.httpx, "get", fail_get)

    with pytest.raises(ValueError, match="blocked internal"):
        web_fetch_module.web_fetch(url)


def test_web_fetch_blocks_dns_that_resolves_to_internal_address(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(None, None, None, None, ("127.0.0.1", 0))]

    def fail_get(*_args, **_kwargs):
        raise AssertionError("network should not be called for blocked DNS targets")

    monkeypatch.setattr(web_fetch_module.socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(web_fetch_module.httpx, "get", fail_get)

    with pytest.raises(ValueError, match="blocked internal"):
        web_fetch_module.web_fetch("http://example.test")
