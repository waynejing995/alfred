import pytest

from agentkit.kernel.providers.errors import ProviderBadRequest
from agentkit.kernel.providers.types import ContentBlock, Message, ToolCall, Usage


def test_message_with_tool_calls_round_trips():
    message = Message(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id="call_1", name="hashread", arguments={"path": "README.md"})],
    )

    dumped = message.model_dump(mode="json")
    restored = Message.model_validate(dumped)

    assert restored.content is None
    assert restored.tool_calls[0].arguments == {"path": "README.md"}


def test_tool_call_from_raw_rejects_malformed_json():
    with pytest.raises(ProviderBadRequest):
        ToolCall.from_raw(id="call_1", name="bad", raw_arguments="{nope")


def test_content_block_cache_control_round_trips():
    block = ContentBlock(text="stable", cache_control={"type": "ephemeral"})

    assert ContentBlock.model_validate(block.model_dump()).cache_control == {"type": "ephemeral"}


def test_usage_defaults_to_zero():
    assert Usage().total_tokens == 0

