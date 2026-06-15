from __future__ import annotations

from agentkit.control.autonomy import AutonomyGate
from agentkit.control.proposals import Proposal, ProposalStore
from agentkit.eval.scorer import score_rollouts
from agentkit.stores.skill.writer import SkillStoreWriter
from agentkit.stores.trace.sqlite import SQLiteTraceStore


class EvolveEngine:
    def __init__(
        self,
        *,
        trace_store: SQLiteTraceStore,
        proposals: ProposalStore,
        writer: SkillStoreWriter,
        gate: AutonomyGate,
        min_replay_rows: int = 1,
    ) -> None:
        self.trace_store = trace_store
        self.proposals = proposals
        self.writer = writer
        self.gate = gate
        self.min_replay_rows = min_replay_rows

    def propose(self, *, skill_name: str, current_body: str) -> str | None:
        if not self.gate.allows_auto():
            return None
        traces = self.trace_store.replay_set(skill_name, min_outcome_quality=0.0)
        if len(traces) < self.min_replay_rows:
            return None
        scores = [
            trace.score if trace.score is not None else float(trace.outcome == "success")
            for trace in traces
        ]
        summary = score_rollouts(scores)
        proposal = Proposal(
            loop="evolve",
            kind="merge",
            payload={
                "skill_name": skill_name,
                "body": _variant_body(current_body, summary.pass_rate),
                "score": summary.model_dump(mode="json"),
                "trace_ids": [trace.trace_id for trace in traces],
            },
        )
        return self.proposals.hold(proposal)

    async def merge(self, proposal_id: str, *, description: str) -> None:
        proposal = self.proposals.decide(proposal_id, accept=True)
        await self.writer.write_skill(
            name=proposal.payload["skill_name"],
            description=description,
            body=proposal.payload["body"],
            origin="evolve",
            metadata={"pass-rate": str(proposal.payload["score"]["pass_rate"])},
        )


def _variant_body(current_body: str, pass_rate: float) -> str:
    return f"{current_body.rstrip()}\n\n<!-- evolved pass_rate={pass_rate:.3f} -->\n"
