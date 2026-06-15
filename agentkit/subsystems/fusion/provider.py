from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict

from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.types import Message, ModelResponse, StreamDelta, ToolDef
from agentkit.subsystems.fusion.aggregator import CodeAggregator


class FusionQuorumError(Exception):
    pass


class FusionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    per_worker_timeout_s: float = 30.0
    quorum: int = 1
    judge_failure: str = "fallback_code"


class FusionProvider(ModelProvider):
    def __init__(
        self,
        workers: list[ModelProvider],
        *,
        aggregator: CodeAggregator | None = None,
        policy: FusionPolicy | None = None,
    ) -> None:
        if not workers:
            raise ValueError("FusionProvider requires at least one worker")
        self.workers = workers
        self.aggregator = aggregator or CodeAggregator()
        self.policy = policy or FusionPolicy()
        self.model = "fusion"

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> ModelResponse:
        outcomes = await asyncio.gather(
            *[
                self._call_worker(worker, messages, tools, tool_choice, params)
                for worker in self.workers
            ]
        )
        responses = [outcome for outcome in outcomes if isinstance(outcome, ModelResponse)]
        if len(responses) < self.policy.quorum:
            raise FusionQuorumError(
                f"fusion quorum unmet: {len(responses)}/{self.policy.quorum} workers succeeded"
            )
        return self.aggregator.aggregate(responses)

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ):
        response = await self.complete(messages, tools=tools, tool_choice=tool_choice, **params)
        if isinstance(response.message.content, str) and response.message.content:
            yield StreamDelta(text=response.message.content)
        yield StreamDelta(
            usage=response.usage,
            finish_reason=response.finish_reason,
            final_response=response,
        )

    async def _call_worker(
        self,
        worker: ModelProvider,
        messages: list[Message],
        tools: list[ToolDef] | None,
        tool_choice: str | None,
        params: dict[str, object],
    ) -> ModelResponse | Exception:
        try:
            return await asyncio.wait_for(
                worker.complete(messages, tools=tools, tool_choice=tool_choice, **params),
                timeout=self.policy.per_worker_timeout_s,
            )
        except Exception as exc:
            return exc

