from agentkit.stores.memory.files import FilesMemoryProvider
from agentkit.stores.memory.types import MemoryContext, MemoryWrite


def test_memory_store_e2e_core_and_project_scoped_prefetch(tmp_path):
    root = tmp_path / "memory"
    (root / "core").mkdir(parents=True)
    (root / "core" / "persona.md").write_text("Alfred is terse.", encoding="utf-8")
    (root / "core" / "user.md").write_text("User prefers uv.", encoding="utf-8")
    provider = FilesMemoryProvider(root)
    provider.sync_turn(
        [
            MemoryWrite(
                op="append",
                target="fact-a",
                text="Project A uses uv and pytest.",
                summary="uv pytest project",
                entities=["uv", "pytest"],
            )
        ],
        MemoryContext(project_id="project-a"),
    )
    provider.sync_turn(
        [
            MemoryWrite(
                op="append",
                target="fact-b",
                text="Project B uses npm.",
                summary="npm project",
                entities=["npm"],
            )
        ],
        MemoryContext(project_id="project-b"),
    )

    retrieved = provider.prefetch(MemoryContext(project_id="project-a", user="uv pytest"))

    assert [block.kind for block in retrieved.blocks[:2]] == ["persona", "user"]
    assert any(block.id == "fact-a" for block in retrieved.blocks)
    assert all(block.id != "fact-b" for block in retrieved.blocks)

