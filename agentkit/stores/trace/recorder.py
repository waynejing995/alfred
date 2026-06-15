from __future__ import annotations

import json
from collections import defaultdict, deque

from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import PostTool, PreTool, TurnEnd
from agentkit.stores.trace.detectors import annotation_from_post_tool
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import SkillRef, StepRecord


class TraceRecorder:
    def __init__(
        self,
        store: SQLiteTraceStore,
        *,
        session_id: str,
        task: str,
        active_skills: list[SkillRef] | None = None,
    ) -> None:
        self.store = store
        self.trace_id = store.start_trace(
            session_id=session_id,
            task=task,
            active_skills=active_skills or [],
        )
        self._seq = 0
        self._pending: dict[tuple[str, str], deque[str]] = defaultdict(deque)

    def attach(self, bus: EventBus) -> None:
        bus.on("pre_tool", self.on_pre_tool)
        bus.on("post_tool", self.on_post_tool)
        bus.on("turn_end", self.on_turn_end)

    def on_pre_tool(self, event: PreTool) -> None:
        self._seq += 1
        step_id = f"{self.trace_id}:{self._seq}"
        args = json.loads(event.args_ref or "{}")
        self.store.append_step(
            StepRecord(
                step_id=step_id,
                trace_id=self.trace_id,
                turn_id=event.turn_id,
                seq=self._seq,
                tool_name=event.tool_name,
                tool_args=args,
            )
        )
        self._pending[(event.turn_id, event.tool_name)].append(step_id)

    def on_post_tool(self, event: PostTool) -> None:
        step_id = self._pop_step_id(event)
        if step_id is None:
            return
        self.store.add_annotation(
            self.trace_id,
            annotation_from_post_tool(event, step_id=step_id),
        )

    def on_turn_end(self, event: TurnEnd) -> None:
        self.store.record_turn(trace_id=self.trace_id, turn_id=event.turn_id)

    def _pop_step_id(self, event: PostTool) -> str | None:
        pending = self._pending.get((event.turn_id, event.tool_name))
        if not pending:
            return None
        return pending.popleft()

