from __future__ import annotations

import fnmatch
import sys
from enum import StrEnum
from typing import Literal

from loguru import logger
from pydantic import BaseModel, ConfigDict


class Permission(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Autonomy(StrEnum):
    OFF = "off"
    ASSIST = "assist"
    AUTO = "auto"


class PermissionDenied(RuntimeError):
    pass


class PermissionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pattern: str
    permission: Permission


class PermissionLayer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    rules: dict[str, list[PermissionRule]]


class PermissionDecision(BaseModel):
    permission: Permission
    layer: str
    bucket: str
    pattern: str = "*"


_STRICTNESS = {
    Permission.ALLOW: 0,
    Permission.ASK: 1,
    Permission.DENY: 2,
}


class PermissionResolver:
    def __init__(self, layers: list[PermissionLayer] | None = None) -> None:
        self.layers = layers or []

    @classmethod
    def default(cls) -> PermissionResolver:
        return cls(
            [
                PermissionLayer(
                    name="default",
                    rules={
                        "read": [PermissionRule(pattern="*", permission=Permission.ALLOW)],
                        "write": [PermissionRule(pattern="*", permission=Permission.ASK)],
                        "bash": [
                            PermissionRule(pattern="*", permission=Permission.ASK),
                            PermissionRule(pattern="rm *", permission=Permission.DENY),
                            PermissionRule(pattern="rm -rf *", permission=Permission.DENY),
                            PermissionRule(pattern="sudo *", permission=Permission.DENY),
                            PermissionRule(pattern="chmod 777 *", permission=Permission.DENY),
                        ],
                        "web_fetch": [PermissionRule(pattern="*", permission=Permission.ASK)],
                    },
                )
            ]
        )

    def with_layer(self, layer: PermissionLayer) -> PermissionResolver:
        return PermissionResolver([*self.layers, layer])

    def resolve(self, *, bucket: str, action: str = "*") -> PermissionDecision:
        decision = PermissionDecision(
            permission=Permission.ALLOW,
            layer="implicit",
            bucket=bucket,
            pattern="*",
        )
        for layer in self.layers:
            layer_decision = self._resolve_layer(layer, bucket=bucket, action=action)
            if layer_decision is None:
                continue
            if _STRICTNESS[layer_decision.permission] >= _STRICTNESS[decision.permission]:
                decision = layer_decision
        return decision

    def assert_allowed(
        self,
        *,
        tool_name: str,
        bucket: str,
        action: str = "*",
        autonomy: Autonomy = Autonomy.ASSIST,
        interactive: bool | None = None,
    ) -> PermissionDecision:
        decision = self.resolve(bucket=bucket, action=action)
        if decision.permission is Permission.ALLOW:
            return decision
        if decision.permission is Permission.DENY:
            logger.warning(
                "permission denied: tool={} bucket={} layer={} pattern={}",
                tool_name,
                bucket,
                decision.layer,
                decision.pattern,
            )
            raise PermissionDenied(
                f"tool {tool_name!r} denied by {decision.layer}:{decision.pattern}"
            )
        if autonomy is Autonomy.AUTO:
            return decision
        if autonomy is Autonomy.OFF:
            raise PermissionDenied(f"tool {tool_name!r} ask treated as deny under autonomy=off")
        interactive = sys.stdin.isatty() if interactive is None else interactive
        if not interactive:
            logger.warning(
                "permission ask downgraded to deny: tool={} bucket={} no interactive channel",
                tool_name,
                bucket,
            )
            raise PermissionDenied(
                f"tool {tool_name!r} requires confirmation but no TTY is present"
            )
        # Tier-0 deliberately does not implement a prompt UI; CLI/server own that surface.
        raise PermissionDenied(f"tool {tool_name!r} requires interactive confirmation")

    @staticmethod
    def strictest(left: Permission, right: Permission) -> Permission:
        return left if _STRICTNESS[left] >= _STRICTNESS[right] else right

    @staticmethod
    def from_allowed_tools(
        allowed_tools: list[str],
        *,
        name: str = "skill",
        all_known_tools: list[str] | None = None,
    ) -> PermissionLayer:
        rules: dict[str, list[PermissionRule]] = {}
        known = all_known_tools or []
        for tool in known:
            rules[tool] = [PermissionRule(pattern="*", permission=Permission.DENY)]
        for tool in allowed_tools:
            rules[tool] = [PermissionRule(pattern="*", permission=Permission.ALLOW)]
        return PermissionLayer(name=name, rules=rules)

    @staticmethod
    def _resolve_layer(
        layer: PermissionLayer,
        *,
        bucket: str,
        action: str,
    ) -> PermissionDecision | None:
        rules = layer.rules.get(bucket) or []
        matched: PermissionRule | None = None
        for rule in rules:
            if fnmatch.fnmatch(action, rule.pattern):
                matched = rule
        if matched is None:
            return None
        return PermissionDecision(
            permission=matched.permission,
            layer=layer.name,
            bucket=bucket,
            pattern=matched.pattern,
        )


def permission_from_literal(value: Literal["allow", "ask", "deny"] | str) -> Permission:
    return Permission(value)
