from agentkit.control.autonomy import AutonomyGate
from agentkit.control.proposals import ProposalStore
from agentkit.kernel.permission import Autonomy
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import SkillRef
from agentkit.subsystems.distill import DistillEngine


def _seed_trace(store: SQLiteTraceStore, skill_name: str) -> str:
    trace_id = store.start_trace(
        session_id="session-1",
        task="task",
        active_skills=[SkillRef(name=skill_name, version="v1")],
    )
    store.seal_trace(trace_id, outcome="success", outcome_source="verifier", score=1.0)
    return trace_id


def test_distill_holds_proposal_and_persists_high_water_mark(tmp_path):
    trace_store = SQLiteTraceStore(tmp_path / "trace.db", project_id="p")
    proposals = ProposalStore(tmp_path / "proposals.json")
    trace_id = _seed_trace(trace_store, "reader")
    engine = DistillEngine(
        trace_store=trace_store,
        proposals=proposals,
        gate=AutonomyGate(Autonomy.AUTO),
    )

    proposal_id = engine.run_once(skill_name="reader")
    second = engine.run_once(skill_name="reader")

    proposal = proposals.decide(proposal_id, accept=False)
    assert proposal.payload["trace_ids"] == [trace_id]
    assert proposal.status == "rejected"
    assert second is None
    assert trace_store.get_meta("distill:reader:seen") == "1"


def test_distill_respects_autonomy_off(tmp_path):
    trace_store = SQLiteTraceStore(tmp_path / "trace.db", project_id="p")
    proposals = ProposalStore(tmp_path / "proposals.json")
    _seed_trace(trace_store, "reader")

    proposal_id = DistillEngine(
        trace_store=trace_store,
        proposals=proposals,
        gate=AutonomyGate(Autonomy.OFF),
    ).run_once(skill_name="reader")

    assert proposal_id is None
    assert proposals.list_pending() == []

