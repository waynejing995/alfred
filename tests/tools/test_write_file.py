from agentkit.tools.list_dir import list_dir
from agentkit.tools.write_file import write_file


def test_write_file_creates_and_overwrites_full_content(tmp_path):
    target = tmp_path / "note.txt"

    first = write_file(str(target), "first")
    second = write_file(str(target), "second\n")

    assert first["bytes_written"] == 5
    assert second["bytes_written"] == 7
    assert target.read_text() == "second\n"


def test_list_dir_lists_directory_entries(tmp_path):
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "a").mkdir()

    entries = list_dir(str(tmp_path))

    assert [entry["name"] for entry in entries] == ["a", "b.txt"]
    assert entries[0]["type"] == "dir"
    assert entries[1]["type"] == "file"
