from __future__ import annotations

from agentkit.subsystems.handoff.payload import HandoffPayload
from agentkit.subsystems.handoff.spawner import AgentSpec, Spawner


async def spawn_subagent(
    spawner: Spawner,
    *,
    role: str,
    objective: str,
    output_format: str = "",
    tool_names: list[str] | None = None,
    from_agent: str = "root",
) -> dict:
    result = await spawner.spawn(
        AgentSpec(role=role, tool_names=tool_names or []),
        HandoffPayload(
            from_agent=from_agent,
            control="returnable",
            objective=objective,
            output_format=output_format,
        ),
    )
    return result.model_dump(mode="json")


async def handoff_to(
    spawner: Spawner,
    *,
    target_role: str,
    objective: str,
    output_format: str = "",
    from_agent: str = "root",
) -> dict:
    result = await spawner.spawn(
        AgentSpec(role=target_role),
        HandoffPayload(
            from_agent=from_agent,
            control="one_way",
            objective=objective,
            output_format=output_format,
        ),
    )
    return result.model_dump(mode="json")

