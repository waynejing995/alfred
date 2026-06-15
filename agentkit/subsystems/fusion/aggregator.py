from __future__ import annotations

import json
from collections import Counter

from agentkit.kernel.providers.types import Message, ModelResponse, Usage


class CodeAggregator:
    def aggregate(self, responses: list[ModelResponse], *, quorum: int = 1) -> ModelResponse:
        tool_response = _majority_tool_response(responses, quorum=quorum)
        if tool_response is not None:
            return tool_response
        content = "\n\n".join(
            str(response.message.content or "")
            for response in responses
            if response.message.content
        )
        return ModelResponse(
            message=Message(role="assistant", content=content),
            usage=_sum_usage(responses),
            finish_reason="stop",
            model="fusion/code",
            raw={"workers": [response.model for response in responses]},
        )


def _majority_tool_response(responses: list[ModelResponse], *, quorum: int) -> ModelResponse | None:
    keyed = []
    for response in responses:
        if not response.message.tool_calls:
            continue
        signature = [
            (call.name, json.dumps(call.arguments, sort_keys=True))
            for call in response.message.tool_calls
        ]
        keyed.append((json.dumps(signature), response))
    if not keyed:
        return None
    winner, count = Counter(key for key, _response in keyed).most_common(1)[0]
    if count < quorum or count <= len(keyed) / 2:
        raise ValueError("fusion tool-call quorum unmet")
    return next(response for key, response in keyed if key == winner)


def _sum_usage(responses: list[ModelResponse]) -> Usage:
    return Usage(
        prompt_tokens=sum(response.usage.prompt_tokens for response in responses),
        completion_tokens=sum(response.usage.completion_tokens for response in responses),
        total_tokens=sum(response.usage.total_tokens for response in responses),
        cached_tokens=sum(response.usage.cached_tokens for response in responses),
        cache_creation_tokens=sum(response.usage.cache_creation_tokens for response in responses),
    )
