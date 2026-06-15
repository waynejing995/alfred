from agentkit.control.autonomy import AutonomyGate
from agentkit.control.proposals import ProposalStore
from agentkit.kernel.permission import Autonomy
from agentkit.stores.skill.loader import build_catalog
from agentkit.stores.skill.writer import SkillStoreWriter
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import SkillRef
from agentkit.subsystems.evolve import EvolveEngine


async def test_evolve_merge_versions_and_revert_restores(tmp_path):
    writer = SkillStoreWriter(tmp_path / "skills")
    await writer.write_skill(
        name="reader",
        description="Use reader.",
        body="Version one",
        origin="human",
    )
    trace_store = SQLiteTraceStore(tmp_path / "trace.db", project_id="p")
    trace_id = trace_store.start_trace(
        session_id="session-1",
        task="task",
        active_skills=[SkillRef(name="reader", version="v1")],
    )
    trace_store.seal_trace(trace_id, outcome="success", outcome_source="verifier", score=1.0)
    proposals = ProposalStore(tmp_path / "proposals.json")
    engine = EvolveEngine(
        trace_store=trace_store,
        proposals=proposals,
        writer=writer,
        gate=AutonomyGate(Autonomy.AUTO),
    )

    proposal_id = engine.propose(skill_name="reader", current_body="Version one")
    await engine.merge(proposal_id, description="Use reader.")
    await writer.revert(name="reader", version="v1")

    assert "Version one" in build_catalog([tmp_path / "skills"]).get("reader").body

