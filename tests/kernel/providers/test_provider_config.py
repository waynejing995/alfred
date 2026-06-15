import pytest

from agentkit.kernel.providers.config import (
    LiteLLMParams,
    build_litellm_provider,
    resolve_env_value,
    resolve_headers_env,
)
from agentkit.kernel.providers.errors import ConfigError


def test_build_litellm_provider_resolves_env_key(monkeypatch):
    monkeypatch.setenv("ALFRED_TEST_KEY", "secret")

    provider = build_litellm_provider(LiteLLMParams(model="mock", env_key="ALFRED_TEST_KEY"))

    assert provider.api_key == "secret"


def test_build_litellm_provider_fails_loud_for_missing_env():
    with pytest.raises(ConfigError):
        build_litellm_provider(LiteLLMParams(model="mock", env_key="NO_SUCH_ENV"))


def test_header_env_interpolation(monkeypatch):
    monkeypatch.setenv("HEADER_SECRET", "value")

    assert resolve_env_value("${HEADER_SECRET}") == "value"


def test_headers_env_parses_colon_separated_headers(monkeypatch):
    monkeypatch.setenv("CUSTOM_HEADERS", "Ocp-Apim-Subscription-Key: secret")

    assert resolve_headers_env("CUSTOM_HEADERS") == {"Ocp-Apim-Subscription-Key": "secret"}
