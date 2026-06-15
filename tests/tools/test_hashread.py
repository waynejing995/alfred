from agentkit.kernel.registries import ToolsRegistry
from agentkit.tools import register_builtin_tools
from agentkit.tools.file_hash import hashread


def test_hashread_emits_line_hash_content_rows(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    rows = hashread(str(target)).splitlines()

    assert rows[0].endswith("|alpha")
    assert rows[1].endswith("|beta")
    assert rows[0].split("|", 1)[0].startswith("1:")
    assert rows[1].split("|", 1)[0].startswith("2:")


def test_hashread_registers_as_read_tool():
    registry = ToolsRegistry()

    register_builtin_tools(registry)

    assert registry.get("hashread").permission_bucket == "read"
