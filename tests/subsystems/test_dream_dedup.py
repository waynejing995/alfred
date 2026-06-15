import shutil

from agentkit.stores.memory.files import FilesMemoryProvider
from agentkit.stores.memory.types import MemoryContext, MemoryWrite
from agentkit.subsystems.dream import DreamEngine


def test_dream_archives_duplicate_facts_not_delete(tmp_path):
    memory = FilesMemoryProvider(tmp_path / "memory")
    ctx = MemoryContext(project_id="p")
    write = MemoryWrite(
        op="append",
        text="duplicate fact",
        summary="duplicate",
        entities=["duplicate"],
    )
    memory.sync_turn([write.model_copy(update={"target": "fact-a"})], ctx)
    source = memory.facts_dir / "fact-a.md"
    shutil.copy2(source, memory.facts_dir / "fact-b.md")

    result = DreamEngine(memory=memory).run_once()

    assert result == {"archived": 1}
    assert (memory.facts_dir / ".archive" / "fact-b.md").exists()
    assert (memory.facts_dir / "fact-a.md").exists()

