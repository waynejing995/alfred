from agentkit.tools import search


def test_fff_falls_back_to_python_grep_with_explicit_warning(tmp_path, monkeypatch):
    target = tmp_path / "sample.txt"
    target.write_text("alpha\nneedle here\n")
    monkeypatch.setattr(search, "_bundled_fff_binary", lambda: None)
    monkeypatch.setattr(search, "_rg_path", lambda: None)

    result = search.fff("needle", str(tmp_path))

    assert result["backend"] == "python"
    assert result["matches"] == [
        {"path": str(target), "line": 2, "text": "needle here"},
    ]
    assert result["warnings"] == [
        "bundled fff unavailable; falling back",
        "rg unavailable; falling back to pure-Python grep",
    ]
