from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from agentkit.kernel.events.defs import BudgetExhausted, BudgetWarning


@dataclass(frozen=True)
class Grant:
    agent_id: str
    n: int
    refundable: bool
    _id: int


BudgetEventHandler = Callable[[BudgetWarning | BudgetExhausted], None]


class IterationBudget:
    """Single-owner, await-free iteration budget."""

    def __init__(
        self,
        total_cap: int,
        warn_at_frac: float = 0.8,
        *,
        on_event: BudgetEventHandler | None = None,
    ) -> None:
        if total_cap < 0:
            raise ValueError("total_cap must be >= 0")
        self._cap = total_cap
        self._remaining = total_cap
        self._spent: dict[str, int] = {}
        self._warn_threshold = int(total_cap * warn_at_frac)
        self._warned = False
        self._seq = 0
        self._refunded: set[int] = set()
        self._on_event = on_event

    def reserve(self, agent_id: str, n: int = 1, *, refundable: bool = False) -> Grant | None:
        if n <= 0:
            raise ValueError("reserve n must be > 0")
        if self._remaining < n:
            self._emit(BudgetExhausted(agent_id=agent_id, cap=self._cap))
            return None
        self._remaining -= n
        self._spent[agent_id] = self._spent.get(agent_id, 0) + n
        self._seq += 1
        if not self._warned and self._cap - self._remaining >= self._warn_threshold:
            self._warned = True
            self._emit(BudgetWarning(agent_id=agent_id, remaining=self._remaining, cap=self._cap))
        return Grant(agent_id=agent_id, n=n, refundable=refundable, _id=self._seq)

    def refund(self, grant: Grant) -> None:
        if grant._id in self._refunded:
            return
        self._refunded.add(grant._id)
        self._remaining += grant.n
        self._spent[grant.agent_id] = self._spent.get(grant.agent_id, 0) - grant.n
        if self._spent[grant.agent_id] <= 0:
            self._spent.pop(grant.agent_id, None)
        if self._cap - self._remaining < self._warn_threshold:
            self._warned = False

    @property
    def remaining(self) -> int:
        return self._remaining

    @property
    def cap(self) -> int:
        return self._cap

    def spent_by(self, agent_id: str) -> int:
        return self._spent.get(agent_id, 0)

    def reconciles(self) -> bool:
        return self._remaining + sum(self._spent.values()) == self._cap

    def _emit(self, event: BudgetWarning | BudgetExhausted) -> None:
        if self._on_event is not None:
            self._on_event(event)

