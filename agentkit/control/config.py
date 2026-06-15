from __future__ import annotations

import os
import re
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentkit.kernel.permission import Autonomy, PermissionLayer
from agentkit.kernel.registries import Registry

_ENV_EXPR = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)}$")


class ConfigError(Exception):
    pass


class ComponentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    params: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_plaintext_secrets(self) -> ComponentSpec:
        _reject_plaintext_secret_keys(self.params)
        return self


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tool_calls: int = 20


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: ComponentSpec
    memory: ComponentSpec | None = None
    skill_sources: list[ComponentSpec] = Field(default_factory=list)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    permission: list[PermissionLayer] = Field(default_factory=list)
    autonomy: Autonomy = Autonomy.ASSIST

    @classmethod
    def from_yaml(cls, *paths: str) -> AgentConfig:
        merged: dict[str, Any] = {}
        for path in paths:
            data = _load_yaml(path)
            merged = deep_merge(merged, data)
        merged = apply_env_overrides(merged)
        merged = interpolate_env(merged)
        return cls.model_validate(merged)


def resolve_component(spec: ComponentSpec, registry: Registry) -> Any:
    entry = registry.get(spec.type)
    params = {
        key: resolve_component(value, registry) if isinstance(value, ComponentSpec) else value
        for key, value in spec.params.items()
    }
    if entry.params_model is not None:
        validated = entry.params_model.model_validate(params)
        return entry.factory(validated)
    return entry.factory(**params)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    if "type" in override and base.get("type") not in (None, override["type"]):
        return dict(override)
    output = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(output.get(key), dict):
            output[key] = deep_merge(output[key], value)
        else:
            output[key] = value
    return output


def interpolate_env(value: Any) -> Any:
    if isinstance(value, str):
        match = _ENV_EXPR.match(value)
        if not match:
            return value
        key = match.group(1)
        resolved = os.environ.get(key)
        if not resolved:
            raise ConfigError(f"environment variable {key!r} referenced by config is unset/empty")
        return resolved
    if isinstance(value, dict):
        return {key: interpolate_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [interpolate_env(item) for item in value]
    return value


def apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    output = dict(data)
    prefix = "ALFRED_"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        path = key[len(prefix) :].lower().split("__")
        output = _set_path(output, path, value)
    return output


def _set_path(data: dict[str, Any], path: list[str], value: Any) -> dict[str, Any]:
    output = dict(data)
    cursor = output
    for part in path[:-1]:
        next_value = cursor.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
        cursor[part] = dict(next_value)
        cursor = cursor[part]
    cursor[path[-1]] = value
    return output


def _load_yaml(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain a mapping: {path}")
    _reject_plaintext_secret_keys(data)
    return data


def _reject_plaintext_secret_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"api_key", "secret", "token"}:
                raise ConfigError(f"plaintext secret key {key!r} is not allowed; use env_key")
            _reject_plaintext_secret_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_plaintext_secret_keys(item)
