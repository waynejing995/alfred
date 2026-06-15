from __future__ import annotations

from agentkit.control.autonomy import AutonomyGate
from agentkit.control.proposals import Proposal, ProposalStore
from agentkit.stores.trace.sqlite import SQLiteTraceStore


class DistillEngine:
    def __init__(
        self,
        *,
        trace_store: SQLiteTraceStore,
        proposals: ProposalStore,
        gate: AutonomyGate,
        batch_min: int = 1,
    ) -> None:
        self.trace_store = trace_store
        self.proposals = proposals
        self.gate = gate
        self.batch_min = batch_min

    def run_once(self, *, skill_name: str) -> str | None:
        if not self.gate.allows_auto():
            return None
        traces = sorted(
            self.trace_store.replay_set(skill_name, min_outcome_quality=0.0),
            key=lambda trace: (trace.started_at, trace.trace_id),
        )
        cursor_key = f"distill:{skill_name}:seen"
        cursor = self.trace_store.get_meta(cursor_key)
        new_traces = [
            trace
            for trace in traces
            if cursor is None or (trace.started_at, trace.trace_id) > _decode_cursor(cursor)
        ]
        if len(new_traces) < self.batch_min:
            return None
        proposal = Proposal(
            loop="distill",
            kind="new_skill",
            payload={
                "skill_name": f"{skill_name}-distilled",
                "body": _body(skill_name, new_traces),
                "trace_ids": [trace.trace_id for trace in new_traces],
            },
        )
        proposal_id = self.proposals.hold(proposal)
        last = new_traces[-1]
        self.trace_store.set_meta(cursor_key, _encode_cursor(last.started_at, last.trace_id))
        return proposal_id


def _body(skill_name: str, traces) -> str:
    return (
        f"# {skill_name} distilled lesson\n\n"
        f"Derived from {len(traces)} replay traces. Preserve the behaviors that led to success."
    )


def _encode_cursor(started_at: float, trace_id: str) -> str:
    return f"{started_at:.9f}|{trace_id}"


def _decode_cursor(cursor: str) -> tuple[float, str]:
    raw_started_at, trace_id = cursor.split("|", 1)
    return float(raw_started_at), trace_id
