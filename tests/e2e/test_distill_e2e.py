from agentkit.control.autonomy import AutonomyGate
from agentkit.control.proposals import ProposalStore
from agentkit.kernel.permission import Autonomy
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import SkillRef
from agentkit.subsystems.distill import DistillEngine


def test_distill_e2e_trace_batch_proposes_gated_skill_and_cursor_survives_restart(tmp_path):
    trace_store = SQLiteTraceStore(tmp_path / "trace.db", project_id="p")
    trace_id = trace_store.start_trace(
        session_id="session-1",
        task="task",
        active_skills=[SkillRef(name="reader", version="v1")],
    )
    trace_store.seal_trace(trace_id, outcome="success", outcome_source="verifier", score=1.0)
    proposal_path = tmp_path / "proposals.json"

    proposal_id = DistillEngine(
        trace_store=trace_store,
        proposals=ProposalStore(proposal_path),
        gate=AutonomyGate(Autonomy.AUTO),
    ).run_once(skill_name="reader")
    restarted = SQLiteTraceStore(tmp_path / "trace.db", project_id="p")
    second = DistillEngine(
        trace_store=restarted,
        proposals=ProposalStore(proposal_path),
        gate=AutonomyGate(Autonomy.AUTO),
    ).run_once(skill_name="reader")

    assert proposal_id is not None
    assert second is None
    assert ProposalStore(proposal_path).list_pending()[0].payload["trace_ids"] == [trace_id]

