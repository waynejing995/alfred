from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import PreTool, SubscriberError, TurnEnd


async def test_exact_and_wildcard_subscribers_fire():
    bus = EventBus()
    seen = []
    bus.on("turn_end", lambda event: seen.append(("exact", event.name)))
    bus.on("*", lambda event: seen.append(("all", event.name)))

    await bus.emit(TurnEnd(session_id="s", turn_id="t"))

    assert seen == [("exact", "turn_end"), ("all", "turn_end")]


async def test_blockable_subscriber_veto_propagates():
    bus = EventBus()

    def veto(_event):
        raise RuntimeError("no")

    bus.on("pre_tool", veto)

    try:
        await bus.emit(PreTool(session_id="s", turn_id="t", tool_name="bash"))
    except RuntimeError as exc:
        assert str(exc) == "no"
    else:
        raise AssertionError("veto did not propagate")


async def test_background_subscriber_error_is_emitted():
    bus = EventBus()
    seen = []

    def failing(_event):
        raise RuntimeError("boom")

    bus.on("turn_end", failing)
    bus.on("subscriber.error", lambda event: seen.append(event))

    await bus.emit(TurnEnd(session_id="s", turn_id="t"))

    assert isinstance(seen[0], SubscriberError)
    assert seen[0].source_event == "turn_end"

