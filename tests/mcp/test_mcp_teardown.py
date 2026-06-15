import sys

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


async def test_mcp_manager_teardown_is_idempotent_enough(tmp_path):
    manager = MCPManager()

    await manager.connect_stdio(
        MCPServerConfig(name="demo", command=sys.executable, args=[str(_server_script(tmp_path))])
    )
    await manager.close()
