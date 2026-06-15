from pydantic import BaseModel

from agentkit.kernel.registries import Registries


def test_five_registries_exist_and_register():
    registries = Registries()

    for registry in [
        registries.events,
        registries.models,
        registries.skill_sources,
        registries.middleware,
    ]:
        registry.register("x", lambda: "ok")
        assert registry.get("x").factory() == "ok"

    registries.tools.register(
        name="noop",
        description="No-op",
        parameters={"type": "object", "properties": {}},
        handler=lambda: "ok",
        permission_bucket="read",
    )

    assert registries.tools.get("noop").to_tool_def().name == "noop"


class Params(BaseModel):
    value: int

