import pytest

from agentkit_eval import Arm, EvalTask, Experiment, run_experiment


def test_parity_guard_rejects_unlisted_differences():
    experiment = Experiment(
        name="bad",
        arms=[
            Arm(name="baseline", config={"model": {"type": "mock"}}),
            Arm(name="changed", config={"model": {"type": "litellm"}}, varies=[]),
        ],
        tasks=[EvalTask(id="t1", prompt="hello", expected="mock: hello")],
    )

    with pytest.raises(ValueError, match="differs outside varies"):
        run_experiment(experiment)

