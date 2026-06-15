import pytest

from agentkit.control.autonomy import (
    AutoLoop,
    AutonomyGate,
    SelfEditForbidden,
    must_confirm,
    validate_self_edit,
)
from agentkit.control.proposals import Proposal, ProposalStore
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.permission import Autonomy


async def test_autonomy_gate_emits_change_and_answers_controls():
    bus = EventBus()
    seen = []
    bus.on("autonomy_changed", lambda event: seen.append((event.old, event.new, event.source)))
    gate = AutonomyGate(Autonomy.ASSIST, bus)

    await gate.set(Autonomy.OFF, source="test")

    assert gate.level is Autonomy.OFF
    assert gate.allows_auto() is False
    assert gate.requires_confirm() is False
    assert seen == [("assist", "off", "test")]


def test_must_confirm_takes_stricter_gate():
    assert must_confirm(AutonomyGate(Autonomy.ASSIST), "auto") is True
    assert must_confirm(AutonomyGate(Autonomy.AUTO), "confirm-required") is True
    assert must_confirm(AutonomyGate(Autonomy.AUTO), "auto") is False


def test_auto_loop_requires_gate():
    with pytest.raises(TypeError):
        AutoLoop(gate=None)


def test_agent_origin_self_edit_cannot_change_autonomy_or_gates():
    with pytest.raises(SelfEditForbidden):
        validate_self_edit({"autonomy": "assist"}, {"autonomy": "auto"}, origin="agent")
    with pytest.raises(SelfEditForbidden):
        validate_self_edit(
            {"gates": {"evolve_merge": "confirm-required"}},
            {"gates": {}},
            origin="agent",
        )

    validate_self_edit({"autonomy": "assist"}, {"autonomy": "auto"}, origin="human")


def test_proposal_store_holds_and_decides(tmp_path):
    store = ProposalStore(tmp_path / "proposals.json")
    proposal_id = store.hold(Proposal(loop="distill", kind="new_skill", payload={"name": "x"}))

    assert [proposal.id for proposal in store.list_pending()] == [proposal_id]
    accepted = store.decide(proposal_id, accept=True)

    assert accepted.status == "accepted"
    assert store.list_pending() == []
