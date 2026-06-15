from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.subsystems.fusion import FusionProvider


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

