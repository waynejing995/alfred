from pathlib import Path


def test_event_serialization_has_no_per_event_cases():
    source = Path("agentkit/kernel/events/base.py").read_text(encoding="utf-8")

    assert "model_dump(mode=\"json\")" in source
    assert "TurnEnd" not in source

