from agentkit.control.autonomy import AutonomyGate
from agentkit.control.proposals import ProposalStore
from agentkit.kernel.permission import Autonomy
from agentkit.stores.skill.writer import SkillStoreWriter
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import SkillRef
from agentkit.subsystems.evolve import EvolveEngine


def _seed_trace(store: SQLiteTraceStore, skill_name: str) -> str:
    trace_id = store.start_trace(
        session_id="session-1",
        task="task",
        active_skills=[SkillRef(name=skill_name, version="v1")],
    )
    store.seal_trace(trace_id, outcome="success", outcome_source="verifier", score=1.0)
    return trace_id


def test_evolve_proposes_variant_from_replay_set(tmp_path):
    trace_store = SQLiteTraceStore(tmp_path / "trace.db", project_id="p")
    proposals = ProposalStore(tmp_path / "proposals.json")
    _seed_trace(trace_store, "reader")

    proposal_id = EvolveEngine(
        trace_store=trace_store,
        proposals=proposals,
        writer=SkillStoreWriter(tmp_path / "skills"),
        gate=AutonomyGate(Autonomy.AUTO),
    ).propose(skill_name="reader", current_body="Use hashread.")

    proposal = proposals.decide(proposal_id, accept=False)
    assert proposal.payload["skill_name"] == "reader"
    assert proposal.payload["score"]["pass_rate"] == 1.0


def test_evolve_skips_without_replay_or_when_autonomy_off(tmp_path):
    trace_store = SQLiteTraceStore(tmp_path / "trace.db", project_id="p")
    proposals = ProposalStore(tmp_path / "proposals.json")

    assert (
        EvolveEngine(
            trace_store=trace_store,
            proposals=proposals,
            writer=SkillStoreWriter(tmp_path / "skills"),
            gate=AutonomyGate(Autonomy.AUTO),
        ).propose(skill_name="reader", current_body="body")
        is None
    )
    _seed_trace(trace_store, "reader")
    assert (
        EvolveEngine(
            trace_store=trace_store,
            proposals=proposals,
            writer=SkillStoreWriter(tmp_path / "skills"),
            gate=AutonomyGate(Autonomy.OFF),
        ).propose(skill_name="reader", current_body="body")
        is None
    )

