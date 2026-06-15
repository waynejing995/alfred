from pathlib import Path

import pytest

from agentkit.kernel.instructions import InstructionReadError, InstructionResolver


def test_global_and_project_agents_merge_global_first_nearest_last(tmp_path, monkeypatch):
    alfred_home = tmp_path / "home"
    alfred_home.mkdir()
    (alfred_home / "AGENTS.md").write_text("global", encoding="utf-8")
    repo = tmp_path / "repo"
    sub = repo / "pkg"
    sub.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "AGENTS.md").write_text("repo", encoding="utf-8")
    (sub / "AGENTS.md").write_text("nearest", encoding="utf-8")
    monkeypatch.setenv("ALFRED_HOME", str(alfred_home))

    resolved = InstructionResolver().resolve(sub)

    assert resolved.merged == "global\n\nrepo\n\nnearest"


def test_agents_wins_over_claude_in_same_directory(tmp_path):
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    (tmp_path / "CLAUDE.md").write_text("claude", encoding="utf-8")

    resolved = InstructionResolver().resolve(tmp_path, alfred_home=tmp_path / "empty")

    assert resolved.merged == "agents"


def test_over_cap_warns_but_keeps_full_content(tmp_path):
    (tmp_path / "AGENTS.md").write_text("abcdef", encoding="utf-8")

    resolved = InstructionResolver(char_cap=2).resolve(tmp_path, alfred_home=tmp_path / "empty")

    assert resolved.over_cap is True
    assert resolved.merged == "abcdef"


def test_declared_unreadable_source_fails_loud(tmp_path):
    missing = tmp_path / "missing.md"

    with pytest.raises(InstructionReadError):
        InstructionResolver().resolve(tmp_path, declared_paths=[missing])

