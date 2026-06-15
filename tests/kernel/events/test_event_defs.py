import pytest

from agentkit.kernel.events.base import Event, serialize
from agentkit.kernel.events.defs import TurnEnd


def test_event_serializes_generically():
    event = TurnEnd(session_id="s", turn_id="t")

    assert serialize(event) == {"type": "turn_end", "payload": {"session_id": "s", "turn_id": "t"}}


def test_invalid_event_name_fails_at_definition():
    with pytest.raises(ValueError):

        class Bad(Event):
            name = "Bad Name"

