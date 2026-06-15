from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict


class ScoreSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    pass_rate: float
    lcb: float


def score_rollouts(scores: list[float]) -> ScoreSummary:
    if not scores:
        return ScoreSummary(count=0, pass_rate=0.0, lcb=0.0)
    mean = sum(scores) / len(scores)
    if len(scores) == 1:
        return ScoreSummary(count=1, pass_rate=mean, lcb=mean)
    variance = sum((score - mean) ** 2 for score in scores) / (len(scores) - 1)
    lcb = max(0.0, mean - 1.96 * math.sqrt(variance) / math.sqrt(len(scores)))
    return ScoreSummary(count=len(scores), pass_rate=mean, lcb=lcb)

