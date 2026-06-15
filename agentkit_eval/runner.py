from __future__ import annotations

from agentkit import Agent
from agentkit.eval import score_rollouts
from agentkit_eval.scorers import score_output
from agentkit_eval.types import Arm, Experiment, Findings, Rollout


def run_experiment(experiment: Experiment) -> dict:
    _validate_parity(experiment.arms)
    rollouts: list[Rollout] = []
    for arm in experiment.arms:
        for task in experiment.tasks:
            for repeat in range(experiment.repeats):
                result = Agent(config=arm.config or None).run_sync(task.prompt)
                output = str(result.message.content or "")
                score = score_output(experiment.scorer, task, output)
                rollouts.append(
                    Rollout(
                        arm=arm.name,
                        task_id=task.id,
                        repeat=repeat,
                        output=output,
                        score=score,
                        cost_tokens=result.usage.total_tokens,
                        trace_id=getattr(result, "trace_id", None),
                    )
                )
    arms = {}
    for arm in experiment.arms:
        arm_rollouts = [rollout for rollout in rollouts if rollout.arm == arm.name]
        summary = score_rollouts([rollout.score.value for rollout in arm_rollouts])
        arms[arm.name] = {
            "score": summary.model_dump(mode="json"),
            "cost_tokens": sum(rollout.cost_tokens for rollout in arm_rollouts),
        }
    return Findings(
        experiment=experiment.name,
        baseline=experiment.arms[0].name,
        repeats=experiment.repeats,
        arms=arms,
        deltas=_deltas(experiment, arms),
        rollouts=rollouts,
    ).model_dump(mode="json")


def _validate_parity(arms: list[Arm]) -> None:
    if len(arms) < 2:
        raise ValueError("experiment requires at least two arms")
    baseline = arms[0].config
    for arm in arms[1:]:
        diffs = _diff_keys(baseline, arm.config)
        allowed = set(arm.varies)
        if not diffs <= allowed:
            raise ValueError(f"arm {arm.name} differs outside varies: {sorted(diffs - allowed)}")


def _diff_keys(left: dict, right: dict, prefix: str = "") -> set[str]:
    diffs = set()
    for key in set(left) | set(right):
        path = f"{prefix}.{key}" if prefix else key
        lval = left.get(key)
        rval = right.get(key)
        if isinstance(lval, dict) and isinstance(rval, dict):
            diffs |= _diff_keys(lval, rval, path)
        elif lval != rval:
            diffs.add(path)
    return diffs


def _deltas(experiment: Experiment, arms: dict) -> list[dict]:
    baseline = experiment.arms[0].name
    base_score = arms[baseline]["score"]["pass_rate"]
    base_cost = arms[baseline]["cost_tokens"]
    output = []
    for arm in experiment.arms[1:]:
        score = arms[arm.name]["score"]["pass_rate"]
        cost = arms[arm.name]["cost_tokens"]
        output.append(
            {
                "arm": arm.name,
                "vs": baseline,
                "success_delta": score - base_score,
                "cost_delta_tokens": cost - base_cost,
            }
        )
    return output
