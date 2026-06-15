from __future__ import annotations

from agentkit_eval.types import EvalTask, Score, ScorerSpec


def score_output(spec: ScorerSpec, task: EvalTask, output: str) -> Score:
    if spec.type == "contains":
        target = task.target or spec.params.get("target", "")
        passed = bool(target) and target in output
        return Score(value=1.0 if passed else 0.0, passed=passed, detail=f"contains {target!r}")
    if spec.type == "exact":
        target = task.target or spec.params.get("target", "")
        passed = output == target
        return Score(value=1.0 if passed else 0.0, passed=passed, detail=f"exact {target!r}")
    raise ValueError(f"unsupported scorer type: {spec.type}")
