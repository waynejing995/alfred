from __future__ import annotations

from agentkit import Agent
from agentkit.eval import score_rollouts
from agentkit_eval.types import Arm, Experiment, Rollout


def run_experiment(experiment: Experiment) -> dict:
    _validate_parity(experiment.arms)
    rollouts: list[Rollout] = []
    for arm in experiment.arms:
        for task in experiment.tasks:
            result = Agent(config=arm.config or None).run_sync(task.prompt)
            output = str(result.message.content or "")
            score = 1.0 if output == task.expected else 0.0
            rollouts.append(
                Rollout(
                    arm=arm.name,
                    task_id=task.id,
                    output=output,
                    score=score,
                    cost_tokens=result.usage.total_tokens,
                )
            )
    arms = {}
    for arm in experiment.arms:
        arm_rollouts = [rollout for rollout in rollouts if rollout.arm == arm.name]
        summary = score_rollouts([rollout.score for rollout in arm_rollouts])
        arms[arm.name] = {
            "score": summary.model_dump(mode="json"),
            "cost_tokens": sum(rollout.cost_tokens for rollout in arm_rollouts),
        }
    return {
        "name": experiment.name,
        "arms": arms,
        "rollouts": [rollout.model_dump(mode="json") for rollout in rollouts],
    }


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

