import sys

from agentkit.kernel.registries import ToolsRegistry
from agentkit.mcp import MCPManager, MCPServerConfig


def _server_script(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(
        """
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo")

@mcp.tool()
def add(left: int, right: int) -> str:
    return str(left + right)

if __name__ == "__main__":
    mcp.run(transport="stdio")
""",
        encoding="utf-8",
    )
    return script


async def test_mcp_manager_e2e_stdio_tool_registration_and_call(tmp_path):
    manager = MCPManager()
    registry = ToolsRegistry()

    await manager.connect_stdio(
        MCPServerConfig(name="math", command=sys.executable, args=[str(_server_script(tmp_path))])
    )
    await manager.register_tools(registry)
    result = await registry.get("math.add").handler(left=2, right=3)
    await manager.close()

    assert result == "5"

