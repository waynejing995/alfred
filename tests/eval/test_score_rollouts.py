from agentkit.eval import score_rollouts


def test_score_rollouts_computes_pass_rate_and_lcb():
    summary = score_rollouts([1.0, 0.0, 1.0, 1.0])

    assert summary.count == 4
    assert summary.pass_rate == 0.75
    assert 0 <= summary.lcb <= summary.pass_rate

