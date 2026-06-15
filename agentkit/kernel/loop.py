from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.context import ContextAssembler
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import PostTool, PreTool, StreamDeltaEvent, TurnEnd, TurnStart
from agentkit.kernel.permission import Autonomy, PermissionDenied, PermissionResolver
from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.types import Message, ModelResponse, ToolCall, Usage
from agentkit.kernel.registries import ToolsRegistry


class ToolResult(BaseModel):
    ok: bool
    body: str
    is_error: bool = False
    refund_ok: bool = False


class VetoError(RuntimeError):
    """A blockable lifecycle subscriber vetoed a tool call."""


class TurnResult(BaseModel):
    message: Message
    history: list[Message]
    usage: Usage = Field(default_factory=Usage)
    stopped: str | None = None
    tool_results: list[ToolResult] = Field(default_factory=list)


@dataclass
class TurnCtx:
    provider: ModelProvider
    tools: ToolsRegistry
    budget: IterationBudget
    bus: EventBus = field(default_factory=EventBus)
    history: list[Message] = field(default_factory=list)
    assembler: ContextAssembler | None = None
    permission: PermissionResolver = field(default_factory=PermissionResolver.default)
    tool_scope: PermissionResolver | None = None
    autonomy: Autonomy = Autonomy.ASSIST
    agent_id: str = "root"
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    interactive: bool = False
    cache_floor_tokens: int = 1024
    turn_index: int = 0
    tool_choice: str | None = None

    def assemble_messages(self) -> list[Message]:
        if self.assembler is None:
            return list(self.history)
        return self.assembler.assemble(self.history).messages


async def run_turn(
    ctx: TurnCtx,
    user_message: str | None = None,
    *,
    stream: bool = False,
) -> TurnResult:
    if user_message is not None:
        ctx.history.append(Message(role="user", content=user_message))
    tool_results: list[ToolResult] = []
    last_response: ModelResponse | None = None
    while True:
        await ctx.bus.emit(TurnStart(session_id=ctx.session_id, turn_id=ctx.turn_id))
        response = await _model_response(ctx, stream=stream)
        last_response = response
        ctx.turn_index += 1
        _warn_if_cache_stuck(ctx, response)
        ctx.history.append(response.message)
        if not response.message.tool_calls:
            await ctx.bus.emit(TurnEnd(session_id=ctx.session_id, turn_id=ctx.turn_id))
            return TurnResult(
                message=response.message,
                history=ctx.history,
                usage=response.usage,
                tool_results=tool_results,
            )
        for call in response.message.tool_calls:
            entry = ctx.tools.get(call.name)
            grant = ctx.budget.reserve(
                ctx.agent_id,
                n=1,
                refundable=entry.refundable,
            )
            if grant is None:
                result = ToolResult(
                    ok=False,
                    body="iteration budget exhausted",
                    is_error=True,
                )
                tool_results.append(result)
                ctx.history.append(_tool_result_message(call, result))
                await ctx.bus.emit(TurnEnd(session_id=ctx.session_id, turn_id=ctx.turn_id))
                return TurnResult(
                    message=response.message,
                    history=ctx.history,
                    usage=response.usage,
                    stopped="budget",
                    tool_results=tool_results,
                )
            result = await dispatch_tool(ctx, call)
            if grant.refundable and result.refund_ok:
                ctx.budget.refund(grant)
            tool_results.append(result)
            ctx.history.append(_tool_result_message(call, result))
        if last_response is None:
            raise RuntimeError("provider did not return a response")


async def dispatch_tool(ctx: TurnCtx, call: ToolCall) -> ToolResult:
    entry = ctx.tools.get(call.name)
    action = _permission_action(call)
    try:
        if ctx.tool_scope is not None:
            ctx.tool_scope.assert_allowed(
                tool_name=entry.name,
                bucket="tool",
                action=entry.name,
                autonomy=ctx.autonomy,
                interactive=ctx.interactive,
            )
        ctx.permission.assert_allowed(
            tool_name=entry.name,
            bucket=entry.permission_bucket,
            action=action,
            autonomy=ctx.autonomy,
            interactive=ctx.interactive,
        )
    except PermissionDenied as exc:
        tool_result = ToolResult(ok=False, body=f"PermissionDenied: {exc}", is_error=True)
        await _emit_post_tool(ctx, entry.name, tool_result)
        return tool_result

    try:
        await ctx.bus.emit(
            PreTool(
                session_id=ctx.session_id,
                turn_id=ctx.turn_id,
                tool_name=entry.name,
                args_ref=json.dumps(call.arguments, sort_keys=True),
            )
        )
    except Exception as exc:
        tool_result = ToolResult(ok=False, body=f"VetoError: {exc}", is_error=True)
        await _emit_post_tool(ctx, entry.name, tool_result)
        return tool_result

    try:
        result = entry.handler(**call.arguments)
        if inspect.isawaitable(result):
            result = await result
        tool_result = ToolResult(ok=True, body=_stringify_tool_output(result))
    except Exception as exc:
        tool_result = ToolResult(ok=False, body=f"{type(exc).__name__}: {exc}", is_error=True)
    await _emit_post_tool(ctx, entry.name, tool_result)
    return tool_result


async def _model_response(ctx: TurnCtx, *, stream: bool) -> ModelResponse:
    tool_choice = ctx.tool_choice
    ctx.tool_choice = None
    if not stream:
        return await ctx.provider.complete(
            ctx.assemble_messages(),
            tools=ctx.tools.tool_defs(),
            tool_choice=tool_choice,
        )

    final_response: ModelResponse | None = None
    async for delta in ctx.provider.stream(
        ctx.assemble_messages(),
        tools=ctx.tools.tool_defs(),
        tool_choice=tool_choice,
    ):
        if delta.text:
            await ctx.bus.emit(StreamDeltaEvent(text=delta.text))
        if delta.final_response is not None:
            final_response = delta.final_response
    if final_response is None:
        raise RuntimeError("streaming provider did not yield a final response")
    return final_response


async def _emit_post_tool(ctx: TurnCtx, tool_name: str, tool_result: ToolResult) -> None:
    await ctx.bus.emit(
        PostTool(
            session_id=ctx.session_id,
            turn_id=ctx.turn_id,
            tool_name=tool_name,
            ok=tool_result.ok,
        )
    )


def _tool_result_message(call: ToolCall, result: ToolResult) -> Message:
    return Message(
        role="tool",
        content=result.body,
        tool_call_id=call.id,
        name=call.name,
    )


def _stringify_tool_output(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _permission_action(call: ToolCall) -> str:
    if call.name == "bash":
        return str(call.arguments.get("command", "*"))
    if call.name == "web_fetch":
        return str(call.arguments.get("url", "*"))
    return "*"


def _warn_if_cache_stuck(ctx: TurnCtx, response: ModelResponse) -> None:
    if ctx.assembler is None or ctx.turn_index < 2 or response.usage.cached_tokens:
        return
    prefix_tokens = ctx.assembler.prefix.token_floor_estimate()
    if prefix_tokens < ctx.cache_floor_tokens:
        logger.debug(
            "prefix below cache floor: prefix={} floor={}",
            prefix_tokens,
            ctx.cache_floor_tokens,
        )
        return
    logger.warning(
        "cache_read stayed 0 on turn {} (prefix={} >= floor={}); prompt cache is not engaging",
        ctx.turn_index,
        prefix_tokens,
        ctx.cache_floor_tokens,
    )


def emit_budget_event_to_bus(bus: EventBus):
    def handler(event):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(bus.emit(event))

    return handler
