from __future__ import annotations

from agentkit.control.autonomy import AutonomyGate
from agentkit.kernel.events.bus import EventBus
from agentkit.subsystems.goal.detector import NoProgressDetector
from agentkit.subsystems.goal.store import GoalStore


class GoalDriver:
    def __init__(
        self,
        *,
        store: GoalStore,
        gate: AutonomyGate,
        detector: NoProgressDetector | None = None,
        max_self_continuations: int = 25,
        bus: EventBus | None = None,
    ) -> None:
        self.store = store
        self.gate = gate
        self.detector = detector or NoProgressDetector()
        self.max_self_continuations = max_self_continuations
        self.bus = bus or EventBus()

    def continuation_message(
        self,
        thread_id: str,
        *,
        assistant_text: str,
        tool_fingerprints: list[str] | None = None,
    ) -> str | None:
        state = self.store.view(thread_id)
        if state is None or state.status != "active":
            return None
        if not self.gate.allows_auto() or self.gate.requires_confirm():
            return None
        if self.detector.observe(
            thread_id,
            tool_fingerprints=tool_fingerprints or [],
            assistant_text=assistant_text,
        ):
            state.status = "no_progress"
            state.reason = "repeated state"
            self.store.update(state)
            return None
        if state.self_continuations >= self.max_self_continuations:
            state.status = "no_progress"
            state.reason = "max_self_continuations"
            self.store.update(state)
            return None
        if state.token_budget is not None and state.tokens_used >= state.token_budget:
            state.status = "budget_limited"
            state.reason = "token_budget"
            self.store.update(state)
            return None
        state.self_continuations += 1
        self.store.update(state)
        return f"continue toward: {state.objective}"

