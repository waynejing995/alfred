from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvalTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    prompt: str
    expected: str


class Arm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    config: dict = Field(default_factory=dict)
    varies: list[str] = Field(default_factory=list)


class Experiment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arms: list[Arm]
    tasks: list[EvalTask]


class Rollout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arm: str
    task_id: str
    output: str
    score: float
    cost_tokens: int

