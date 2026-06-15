from agentkit.stores.memory.files import FilesMemoryProvider
from agentkit.stores.memory.types import MemoryContext, MemoryWrite


def test_rrf_uses_bm25_summary_and_entity_signals(tmp_path):
    provider = FilesMemoryProvider(tmp_path / "memory")
    ctx = MemoryContext(project_id="project-a")
    provider.sync_turn(
        [
            MemoryWrite(
                op="append",
                target="fact-python",
                text="Use uv run python for all Python commands.",
                summary="python uv commands",
                entities=["python", "uv"],
            ),
            MemoryWrite(
                op="append",
                target="fact-rust",
                text="Use cargo for Rust commands.",
                summary="rust cargo commands",
                entities=["rust", "cargo"],
            ),
        ],
        ctx,
    )

    result = provider.search("python uv", project_id="project-a")

    assert [block.id for block in result.blocks] == ["fact-python"]


def test_search_without_project_id_does_not_leak_project_facts(tmp_path):
    provider = FilesMemoryProvider(tmp_path / "memory")
    ctx = MemoryContext(project_id="project-a")
    provider.sync_turn(
        [
            MemoryWrite(
                op="append",
                target="fact-a",
                text="Secret project fact",
                summary="secret",
                entities=["secret"],
            )
        ],
        ctx,
    )

    assert provider.search("secret").blocks == []

