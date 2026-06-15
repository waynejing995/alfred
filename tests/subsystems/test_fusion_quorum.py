import asyncio

import pytest

from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message
from agentkit.subsystems.fusion import FusionPolicy, FusionProvider, FusionQuorumError


class SlowProvider(ModelProvider):
    async def complete(self, messages, tools=None, tool_choice=None, **params):
        await asyncio.sleep(0.05)
        return await MockProvider(["slow"]).complete(messages)

    async def stream(self, messages, tools=None, tool_choice=None, **params):
        yield


async def test_fusion_quorum_uses_surviving_workers():
    provider = FusionProvider(
        [MockProvider(["fast"]), SlowProvider()],
        policy=FusionPolicy(per_worker_timeout_s=0.01, quorum=1),
    )

    response = await provider.complete([Message(role="user", content="hello")])

    assert response.message.content == "fast"


async def test_fusion_quorum_failure_raises_clean_error():
    provider = FusionProvider(
        [SlowProvider()],
        policy=FusionPolicy(per_worker_timeout_s=0.01, quorum=1),
    )

    with pytest.raises(FusionQuorumError):
        await provider.complete([Message(role="user", content="hello")])

