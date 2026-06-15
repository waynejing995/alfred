from __future__ import annotations

import json
import time
from pathlib import Path

from agentkit.subsystems.goal.types import GoalState, GoalStatus


class GoalStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    def set(
        self,
        thread_id: str,
        objective: str,
        *,
        token_budget: int | None = None,
    ) -> GoalState:
        state = GoalState(thread_id=thread_id, objective=objective, token_budget=token_budget)
        self._write(state)
        return state

    def view(self, thread_id: str) -> GoalState | None:
        path = self._path(thread_id)
        if not path.exists():
            return None
        return GoalState.model_validate(json.loads(path.read_text(encoding="utf-8")))

    def pause(self, thread_id: str) -> GoalState:
        return self._set_status(thread_id, "paused")

    def resume(self, thread_id: str) -> GoalState:
        state = self._set_status(thread_id, "active")
        state.self_continuations = 0
        self._write(state)
        return state

    def clear(self, thread_id: str) -> None:
        self._path(thread_id).unlink(missing_ok=True)

    def complete(self, thread_id: str) -> GoalState:
        return self._set_status(thread_id, "complete")

    def block(self, thread_id: str, reason: str) -> GoalState:
        return self._set_status(thread_id, "blocked", reason=reason)

    def update(self, state: GoalState) -> None:
        state.updated_at = time.time()
        self._write(state)

    def _set_status(
        self,
        thread_id: str,
        status: GoalStatus,
        *,
        reason: str | None = None,
    ) -> GoalState:
        state = self.view(thread_id)
        if state is None:
            raise KeyError(f"no goal for thread: {thread_id}")
        state.status = status
        state.reason = reason
        self.update(state)
        return state

    def _write(self, state: GoalState) -> None:
        path = self._path(state.thread_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state.model_dump(mode="json"), indent=2, sort_keys=True))
        tmp.replace(path)

    def _path(self, thread_id: str) -> Path:
        return self.root / f"{thread_id}.json"

