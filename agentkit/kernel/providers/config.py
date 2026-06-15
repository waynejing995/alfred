from __future__ import annotations

import json
import os
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentkit.kernel.providers.errors import ConfigError

_ENV_EXPR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)}$")


class LiteLLMParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    env_key: str | None = None
    base_url: str | None = None
    http_headers: dict[str, str] = Field(default_factory=dict)
    http_headers_env: str | None = None
    query_params: dict[str, str] = Field(default_factory=dict)
    api_version: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    params: dict[str, Any] = Field(default_factory=dict)


def resolve_env_value(value: str) -> str:
    match = _ENV_EXPR.match(value)
    if not match:
        return value
    key = match.group(1)
    resolved = os.environ.get(key)
    if not resolved:
        raise ConfigError(f"environment variable {key!r} referenced by config is unset/empty")
    return resolved


def build_litellm_provider(params: LiteLLMParams):
    from agentkit.kernel.providers.litellm_provider import LiteLLMProvider

    api_key = None
    if params.env_key:
        api_key = os.environ.get(params.env_key)
        if not api_key:
            raise ConfigError(
                f"env_key {params.env_key!r} referenced by model config is unset/empty"
            )
    query_params = dict(params.query_params)
    if params.api_version and "api-version" not in query_params:
        query_params["api-version"] = params.api_version
    headers = {}
    if params.http_headers_env:
        headers.update(resolve_headers_env(params.http_headers_env))
    headers.update({key: resolve_env_value(value) for key, value in params.http_headers.items()})
    return LiteLLMProvider(
        model=params.model,
        api_key=api_key,
        base_url=params.base_url,
        http_headers=headers,
        query_params=query_params,
        extra=params.extra,
    )


def resolve_headers_env(env_key: str) -> dict[str, str]:
    raw = os.environ.get(env_key)
    if not raw:
        raise ConfigError(f"headers env {env_key!r} referenced by config is unset/empty")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return {str(key): str(value) for key, value in parsed.items()}

    headers: dict[str, str] = {}
    for part in re.split(r"[\n;]+", raw):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            key, value = part.split(":", 1)
        elif "=" in part:
            key, value = part.split("=", 1)
        else:
            raise ConfigError(f"headers env {env_key!r} contains a malformed header entry")
        headers[key.strip()] = value.strip()
    if not headers:
        raise ConfigError(f"headers env {env_key!r} did not contain any headers")
    return headers
