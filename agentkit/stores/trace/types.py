from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AnnotationKind = Literal[
    "success",
    "failure",
    "user_pushback",
    "correction",
    "off_track",
    "user_approval",
]
AnnotationSource = Literal["user", "auto", "verifier", "judge"]
AnnotationTarget = Literal["trajectory", "turn", "step"]
TraceOutcome = Literal["success", "failure", "partial", "aborted", "unknown"]
OutcomeSource = Literal["verifier", "user", "judge", "auto", "none"]
AgentRole = Literal["main", "subagent", "worker"]
StepKind = Literal["tool_call", "reasoning", "decision", "state"]
ResultStatus = Literal["ok", "error", "vetoed"]


class SkillRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "active"


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: AnnotationKind
    source: AnnotationSource
    confidence: float = 1.0
    target: AnnotationTarget
    target_id: str
    evidence: str | None = None
    detector: str | None = None


class StepRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    trace_id: str
    turn_id: str | None = None
    seq: int
    kind: StepKind = "tool_call"
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    tool_result: str | dict[str, Any] | None = None
    result_status: ResultStatus = "ok"
    error: dict[str, Any] | None = None
    msg_id: int | None = None
    latency_ms: int | None = None
    budget_after: int | None = None


class TurnRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_id: str
    trace_id: str
    seq: int
    assistant_msg_id: int | None = None
    turn_outcome: TraceOutcome | None = None


class TraceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    project_id: str
    session_id: str
    parent_trace_id: str | None = None
    agent_role: AgentRole = "main"
    task: str = ""
    outcome: TraceOutcome = "unknown"
    outcome_source: OutcomeSource = "none"
    score: float | None = None
    feedback: str | None = None
    body_path: str
    started_at: float
    ended_at: float | None = None
    budget_used: int = 0
    handoff_payload: dict[str, Any] | None = None
    sealed: bool = False
    active_skills: list[SkillRef] = Field(default_factory=list)
    used_skills: list[SkillRef] = Field(default_factory=list)
    steps: list[StepRecord] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)

