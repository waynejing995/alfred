from pydantic import ValidationError

from agentkit.kernel.context import ContextAssembler, FrozenPrefix
from agentkit.kernel.providers.types import Message


def test_frozen_prefix_is_immutable_and_breakpoint_at_end():
    prefix = FrozenPrefix.build(persona="p", user="u", project_instructions="rules")
    assembler = ContextAssembler(prefix)

    prompt = assembler.assemble([Message(role="user", content="hi")])

    system = prompt.messages[0]
    assert system.role == "system"
    assert system.content[-1].cache_control == {"type": "ephemeral"}
    assert prompt.prefix_fingerprint == prefix.fingerprint

    try:
        prefix.persona = "new"
    except ValidationError:
        pass
    else:
        raise AssertionError("FrozenPrefix mutation did not fail")


def test_prefix_dirty_rolls_epoch():
    assembler = ContextAssembler(FrozenPrefix.build(persona="old"))
    old = assembler.prefix.fingerprint

    assembler.mark_dirty()
    assert assembler.roll_epoch_if_dirty(persona="new") is True

    assert assembler.epoch == 1
    assert assembler.prefix.fingerprint != old

