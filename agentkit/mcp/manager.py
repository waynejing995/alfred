from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, ConfigDict, Field

from agentkit.kernel.registries import ToolsRegistry


class MCPServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None


class MCPManager:
    def __init__(self) -> None:
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}

    async def connect_stdio(self, config: MCPServerConfig) -> None:
        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env=config.env,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._sessions[config.name] = session

    async def register_tools(self, registry: ToolsRegistry) -> None:
        for server_name, session in self._sessions.items():
            result = await session.list_tools()
            for tool in result.tools:
                registry.register(
                    name=f"{server_name}.{tool.name}",
                    description=tool.description or f"MCP tool {server_name}.{tool.name}",
                    parameters=tool.inputSchema,
                    handler=_handler(session, tool.name),
                    permission_bucket="mcp",
                )

    async def close(self) -> None:
        await self._stack.aclose()


def _handler(session: ClientSession, tool_name: str):
    async def call(**arguments: Any) -> str:
        result = await session.call_tool(tool_name, arguments)
        if result.isError:
            return f"MCPError: {_content_text(result.content)}"
        return _content_text(result.content)

    return call


def _content_text(content: list[Any]) -> str:
    parts = []
    for item in content:
        text = getattr(item, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(item))
    return "\n".join(parts)

