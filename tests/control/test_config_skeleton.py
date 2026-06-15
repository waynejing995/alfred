import pytest
from pydantic import ValidationError

from agentkit.control.config import AgentConfig, ComponentSpec, resolve_component
from agentkit.kernel.registries import Registry, UnknownComponentType


def test_agent_config_forbids_extra_keys():
    with pytest.raises(ValidationError):
        AgentConfig(model=ComponentSpec(type="mock"), typo=True)


def test_resolve_component_uses_registry():
    registry = Registry("models")
    registry.register("mock", lambda value=1: {"value": value})

    assert resolve_component(
        ComponentSpec(type="mock", params={"value": 2}),
        registry,
    ) == {"value": 2}


def test_unknown_component_type_fails_loud():
    with pytest.raises(UnknownComponentType):
        resolve_component(ComponentSpec(type="missing"), Registry("models"))
