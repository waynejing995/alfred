from agentkit.stores.memory.files import FilesMemoryProvider
from agentkit.stores.memory.types import MemoryContext, MemoryWrite
from agentkit.tools.memory import memory_append, memory_replace


def test_prefetch_returns_core_and_project_scoped_facts(tmp_path):
    root = tmp_path / "memory"
    (root / "core").mkdir(parents=True)
    (root / "core" / "persona.md").write_text("Alfred persona", encoding="utf-8")
    (root / "core" / "user.md").write_text("User likes uv", encoding="utf-8")
    provider = FilesMemoryProvider(root)
    ctx_a = MemoryContext(project_id="project-a", user="uv python")
    ctx_b = MemoryContext(project_id="project-b", user="uv python")
    provider.sync_turn(
        [
            MemoryWrite(
                op="append",
                target="fact-a",
                text="Use uv for Python commands.",
                summary="uv python command preference",
                entities=["uv", "python"],
            )
        ],
        ctx_a,
    )
    provider.sync_turn(
        [
            MemoryWrite(
                op="append",
                target="fact-b",
                text="Other project fact about uv.",
                summary="uv other project",
                entities=["uv"],
            )
        ],
        ctx_b,
    )

    retrieved = provider.prefetch(ctx_a)

    assert [block.kind for block in retrieved.blocks[:2]] == ["persona", "user"]
    assert any(block.id == "fact-a" for block in retrieved.blocks)
    assert all(block.id != "fact-b" for block in retrieved.blocks)


def test_memory_append_is_durable_but_existing_snapshot_is_unchanged(tmp_path):
    provider = FilesMemoryProvider(tmp_path / "memory")
    ctx = MemoryContext(project_id="project-a", user="first")
    before = provider.prefetch(ctx)

    memory_append(
        provider,
        ctx=ctx,
        text="Remember the user likes deterministic tests.",
        summary="deterministic tests",
        entities=["tests"],
    )
    after = provider.search("deterministic", project_id="project-a")

    assert before.blocks == []
    assert after.blocks[0].text == "Remember the user likes deterministic tests."


def test_memory_replace_updates_fact(tmp_path):
    provider = FilesMemoryProvider(tmp_path / "memory")
    ctx = MemoryContext(project_id="project-a")
    memory_append(
        provider,
        ctx=ctx,
        text="old fact",
        summary="old",
        entities=["old"],
        source_session="s1",
    )
    target = provider.search("old", project_id="project-a").blocks[0].id

    memory_replace(
        provider,
        ctx=ctx,
        target=target,
        text="new fact",
        summary="new",
        entities=["new"],
    )

    assert provider.search("new", project_id="project-a").blocks[0].text == "new fact"


def test_corrupt_fact_is_skipped_with_good_facts_still_loaded(tmp_path):
    root = tmp_path / "memory"
    (root / "facts").mkdir(parents=True)
    (root / "facts" / "bad.md").write_text("not frontmatter", encoding="utf-8")
    provider = FilesMemoryProvider(root)
    ctx = MemoryContext(project_id="project-a")

    memory_append(
        provider,
        ctx=ctx,
        text="good fact",
        summary="good",
        entities=["good"],
    )

    assert provider.search("good", project_id="project-a").blocks[0].text == "good fact"
