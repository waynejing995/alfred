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
def echo(text: str) -> str:
    return "echo:" + text

if __name__ == "__main__":
    mcp.run(transport="stdio")
""",
        encoding="utf-8",
    )
    return script


async def test_mcp_tool_registers_and_calls(tmp_path):
    manager = MCPManager()
    registry = ToolsRegistry()

    await manager.connect_stdio(
        MCPServerConfig(name="demo", command=sys.executable, args=[str(_server_script(tmp_path))])
    )
    await manager.register_tools(registry)
    result = await registry.get("demo.echo").handler(text="hello")
    await manager.close()

    assert result == "echo:hello"

