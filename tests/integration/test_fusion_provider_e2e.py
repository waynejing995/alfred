import asyncio

import pytest

from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message
from agentkit.subsystems.fusion import FusionPolicy, FusionProvider, FusionQuorumError


class HangingProvider(ModelProvider):
    async def complete(self, messages, tools=None, tool_choice=None, **params):
        await asyncio.sleep(0.05)
        return await MockProvider(["late"]).complete(messages)

    async def stream(self, messages, tools=None, tool_choice=None, **params):
        yield


async def test_fusion_provider_e2e_timeout_quorum_and_failure_paths():
    surviving = FusionProvider(
        [MockProvider(["ok"]), HangingProvider()],
        policy=FusionPolicy(per_worker_timeout_s=0.01, quorum=1),
    )
    failing = FusionProvider(
        [HangingProvider()],
        policy=FusionPolicy(per_worker_timeout_s=0.01, quorum=1),
    )

    response = await surviving.complete([Message(role="user", content="hello")])
    with pytest.raises(FusionQuorumError):
        await failing.complete([Message(role="user", content="hello")])

    assert response.message.content == "ok"

