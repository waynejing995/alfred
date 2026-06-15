from __future__ import annotations

from agentkit.kernel.registries import ToolsRegistry
from agentkit.tools.bash import bash
from agentkit.tools.file_hash import hashedit, hashread
from agentkit.tools.list_dir import list_dir
from agentkit.tools.search import fff
from agentkit.tools.web_fetch import web_fetch
from agentkit.tools.write_file import write_file


def register_builtin_tools(registry: ToolsRegistry) -> None:
    registry.register(
        name="hashread",
        description="Read a text file as LINE:HASH|content rows.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        handler=hashread,
        permission_bucket="read",
    )
    registry.register(
        name="hashedit",
        description="Edit lines only when their hashread anchors still match.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "edits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "line": {"type": "integer"},
                            "hash": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["line", "hash", "content"],
                    },
                },
            },
            "required": ["path", "edits"],
        },
        handler=hashedit,
        permission_bucket="write",
    )
    registry.register(
        name="write_file",
        description="Create or overwrite a file with full content.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        handler=write_file,
        permission_bucket="write",
    )
    registry.register(
        name="fff",
        description="Search files using bundled fff, rg, then pure-Python grep fallback.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string", "default": "."},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
        handler=fff,
        permission_bucket="read",
    )
    registry.register(
        name="list_dir",
        description="List directory entries.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
        },
        handler=list_dir,
        permission_bucket="read",
    )
    registry.register(
        name="bash",
        description="Run a shell command and return stdout, stderr, and returncode.",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["command"],
        },
        handler=bash,
        permission_bucket="bash",
    )
    registry.register(
        name="web_fetch",
        description="Fetch an HTTP(S) URL after blocking internal SSRF targets.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "timeout": {"type": "number", "default": 10.0},
            },
            "required": ["url"],
        },
        handler=web_fetch,
        permission_bucket="web_fetch",
    )


__all__ = [
    "bash",
    "fff",
    "hashedit",
    "hashread",
    "list_dir",
    "register_builtin_tools",
    "web_fetch",
    "write_file",
]
