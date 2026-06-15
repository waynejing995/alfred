from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agentkit.kernel.permission import Autonomy, PermissionLayer
from agentkit.kernel.registries import Registry


class ComponentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    params: dict[str, Any] = Field(default_factory=dict)


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
