from __future__ import annotations

import time
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

GoalStatus = Literal[
    "active",
    "paused",
    "blocked",
    "usage_limited",
    "budget_limited",
    "complete",
    "no_progress",
]


class GoalState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    goal_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    objective: str
    status: GoalStatus = "active"
    token_budget: int | None = None
    tokens_used: int = 0
    time_used_seconds: int = 0
    self_continuations: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    reason: str | None = None

