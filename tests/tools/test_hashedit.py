import pytest

from agentkit.kernel.registries import ToolsRegistry
from agentkit.tools import register_builtin_tools
from agentkit.tools.file_hash import hashedit, hashread


def _anchor(row: str) -> tuple[int, str]:
    prefix, _content = row.split("|", 1)
    line, digest = prefix.split(":", 1)
    return int(line), digest


def test_hashread_emits_line_hash_content_rows(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\n")

    rows = hashread(str(target)).splitlines()

    assert rows[0].endswith("|alpha")
    assert rows[1].endswith("|beta")
    assert rows[0].split("|", 1)[0].startswith("1:")
    assert rows[1].split("|", 1)[0].startswith("2:")


def test_hashedit_applies_when_anchor_matches(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\n")
    line, digest = _anchor(hashread(str(target)).splitlines()[1])

    diff = hashedit(str(target), [{"line": line, "hash": digest, "content": "bravo"}])

    assert target.read_text() == "alpha\nbravo\n"
    assert "- 2:" in diff
    assert "|beta" in diff
    assert "+ 2:" in diff
    assert "|bravo" in diff


def test_hashedit_rejects_stale_anchor_without_writing(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nbeta\n")
    line, digest = _anchor(hashread(str(target)).splitlines()[0])
    target.write_text("changed\nbeta\n")

    with pytest.raises(ValueError, match="stale edit"):
        hashedit(str(target), [{"line": line, "hash": digest, "content": "new"}])

    assert target.read_text() == "changed\nbeta\n"


def test_builtin_tool_permission_buckets_are_correct():
    registry = ToolsRegistry()

    register_builtin_tools(registry)

    assert registry.get("hashread").permission_bucket == "read"
    assert registry.get("fff").permission_bucket == "read"
    assert registry.get("list_dir").permission_bucket == "read"
    assert registry.get("hashedit").permission_bucket == "write"
    assert registry.get("write_file").permission_bucket == "write"
    assert registry.get("bash").permission_bucket == "bash"
    assert registry.get("web_fetch").permission_bucket == "web_fetch"
