from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.subsystems.fusion import FusionPolicy, FusionProvider


async def test_fusion_tool_call_vote_returns_worker_response_verbatim():
    call = Message(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id="call_1", name="hashread", arguments={"path": "a.txt"})],
    )
    provider = FusionProvider([MockProvider([call]), MockProvider([call])])

    response = await provider.complete([Message(role="user", content="read")])

    assert response.message.tool_calls[0].id == "call_1"
    assert response.message.tool_calls[0].arguments == {"path": "a.txt"}


async def test_fusion_tool_call_vote_requires_majority():
    left = Message(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id="call_1", name="hashread", arguments={"path": "a.txt"})],
    )
    right = Message(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id="call_2", name="hashread", arguments={"path": "b.txt"})],
    )
    provider = FusionProvider(
        [MockProvider([left]), MockProvider([right])],
        policy=FusionPolicy(quorum=2),
    )

    try:
        await provider.complete([Message(role="user", content="read")])
    except ValueError as exc:
        assert "tool-call quorum" in str(exc)
    else:
        raise AssertionError("tool-call disagreement did not fail")
