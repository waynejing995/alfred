from agentkit_eval import Arm, EvalTask, Experiment, run_experiment


def test_run_experiment_scores_arms_and_costs():
    experiment = Experiment(
        name="smoke",
        arms=[
            Arm(name="baseline", config={"model": {"type": "mock"}}),
            Arm(name="same", config={"model": {"type": "mock"}}, varies=[]),
        ],
        tasks=[EvalTask(id="t1", prompt="hello", expected="mock: hello")],
    )

    result = run_experiment(experiment)

    assert result["arms"]["baseline"]["score"]["pass_rate"] == 1.0
    assert result["arms"]["same"]["cost_tokens"] > 0

