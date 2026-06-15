from __future__ import annotations

from agentkit.kernel.registries import ToolsRegistry
from agentkit.tools.file_hash import hashread


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


__all__ = ["hashread", "register_builtin_tools"]
