from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.events.defs import BudgetExhausted, BudgetWarning


def test_reserve_refund_reconciles_and_is_idempotent():
    budget = IterationBudget(2)
    grant = budget.reserve("root", refundable=True)

    assert grant is not None
    assert budget.reconciles()

    budget.refund(grant)
    budget.refund(grant)

    assert budget.remaining == 2
    assert budget.spent_by("root") == 0
    assert budget.reconciles()


def test_exhausted_returns_none_and_emits_event():
    events = []
    budget = IterationBudget(1, on_event=events.append)

    assert budget.reserve("root") is not None
    assert budget.reserve("root") is None

    assert any(isinstance(event, BudgetExhausted) for event in events)


def test_warning_fires_once_when_threshold_crossed():
    events = []
    budget = IterationBudget(10, warn_at_frac=0.5, on_event=events.append)

    for _ in range(6):
        budget.reserve("root")

    assert sum(isinstance(event, BudgetWarning) for event in events) == 1

