from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvalTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str
    target: str | None = None
    setup: dict = Field(default_factory=dict)
    metadata: dict = Field(default_factory=dict)


class ScorerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "contains"
    params: dict = Field(default_factory=dict)


class Score(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: float
    passed: bool
    detail: str = ""


class Arm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    config: dict = Field(default_factory=dict)
    varies: list[str] = Field(default_factory=list)


class Experiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arms: list[Arm]
    task_set: list[EvalTask] = Field(default_factory=list)
    scorer: ScorerSpec = Field(default_factory=ScorerSpec)
    repeats: int = 5
    seed: int = 0

    @property
    def tasks(self) -> list[EvalTask]:
        return self.task_set


class Rollout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arm: str
    task_id: str
    repeat: int = 0
    output: str
    score: Score
    cost_tokens: int
    trace_id: str | None = None


class Findings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experiment: str
    baseline: str
    repeats: int
    arms: dict
    deltas: list[dict] = Field(default_factory=list)
    rollouts: list[Rollout]
