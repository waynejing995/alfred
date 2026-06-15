from __future__ import annotations

from typing import Any

from agentkit.kernel.events.base import Event


class SessionStart(Event):
    name = "session_start"

    session_id: str
    epoch: int = 0
    manifest: dict[str, Any] | None = None


class TurnStart(Event):
    name = "turn_start"

    session_id: str
    turn_id: str


class PreTool(Event):
    name = "pre_tool"
    blockable = True

    session_id: str
    turn_id: str
    tool_name: str
    args_ref: str = ""


class PostTool(Event):
    name = "post_tool"

    session_id: str
    turn_id: str
    tool_name: str
    ok: bool


class TurnEnd(Event):
    name = "turn_end"

    session_id: str
    turn_id: str


class SessionEnd(Event):
    name = "session_end"

    session_id: str
    reason: str = "normal"


class Idle(Event):
    name = "idle"

    session_id: str


class Tick(Event):
    name = "tick"

    source: str = "scheduler"


class JobDue(Event):
    name = "job_due"

    job_id: str


class SkillUsed(Event):
    name = "skill_used"

    skill_name: str
    session_id: str


class BudgetWarning(Event):
    name = "budget_warning"

    agent_id: str
    remaining: int
    cap: int


class BudgetExhausted(Event):
    name = "budget_exhausted"

    agent_id: str
    cap: int


class Handoff(Event):
    name = "handoff"

    session_id: str
    payload_ref: str


class AutonomyChanged(Event):
    name = "autonomy_changed"

    old: str
    new: str
    source: str


class SubscriberError(Event):
    name = "subscriber.error"

    source_event: str
    handler: str
    error_type: str
    message: str


class StreamDeltaEvent(Event):
    name = "stream_delta"

    text: str
