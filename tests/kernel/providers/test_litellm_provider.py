from pathlib import Path

from agentkit.kernel.providers.litellm_provider import LiteLLMProvider
from agentkit.kernel.providers.types import ContentBlock, Message


def test_openai_model_strips_cache_control():
    provider = LiteLLMProvider(model="openai/gpt-4.1", api_key="x")

    payload = provider._to_litellm_messages(
        [
            Message(
                role="system",
                content=[ContentBlock(text="stable", cache_control={"type": "ephemeral"})],
            )
        ]
    )

    assert "cache_control" not in payload[0]["content"][0]


def test_anthropic_model_passes_cache_control():
    provider = LiteLLMProvider(model="claude-sonnet-4-5", api_key="x")

    payload = provider._to_litellm_messages(
        [
            Message(
                role="system",
                content=[ContentBlock(text="stable", cache_control={"type": "ephemeral"})],
            )
        ]
    )

    assert payload[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_named_tool_choice_expands_to_function_choice():
    assert LiteLLMProvider._to_tool_choice("hashread") == {
        "type": "function",
        "function": {"name": "hashread"},
    }


def test_only_litellm_provider_imports_litellm():
    root = Path("agentkit")
    hits = [
        path
        for path in root.rglob("*.py")
        if "import litellm" in path.read_text(encoding="utf-8")
        or "from litellm" in path.read_text(encoding="utf-8")
    ]

    assert hits == [Path("agentkit/kernel/providers/litellm_provider.py")]
