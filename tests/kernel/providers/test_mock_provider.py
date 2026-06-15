from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message


async def test_mock_provider_complete_returns_usage():
    provider = MockProvider(["hello"])

    response = await provider.complete([Message(role="user", content="say hi")])

    assert response.message.content == "hello"
    assert response.usage.total_tokens > 0
    assert response.model == "mock"


async def test_mock_provider_stream_yields_final_response():
    provider = MockProvider(["hello"])

    deltas = [delta async for delta in provider.stream([Message(role="user", content="say hi")])]

    assert deltas[0].text == "hello"
    assert deltas[-1].final_response is not None

