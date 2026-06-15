import shutil

from agentkit.stores.memory.files import FilesMemoryProvider
from agentkit.stores.memory.types import MemoryContext, MemoryWrite
from agentkit.subsystems.dream import DreamEngine


def test_dream_e2e_archives_duplicate_memory_and_leaves_skills_untouched(tmp_path):
    memory = FilesMemoryProvider(tmp_path / "memory")
    ctx = MemoryContext(project_id="p")
    memory.sync_turn(
        [
            MemoryWrite(
                op="append",
                target="fact-a",
                text="duplicate fact",
                summary="duplicate",
                entities=["duplicate"],
            )
        ],
        ctx,
    )
    shutil.copy2(memory.facts_dir / "fact-a.md", memory.facts_dir / "fact-b.md")
    skill = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo-skill\ndescription: Skill.\n---\nbody\n", encoding="utf-8")
    before = skill.read_text(encoding="utf-8")

    result = DreamEngine(memory=memory).run_once()

    assert result["archived"] == 1
    assert (memory.facts_dir / ".archive" / "fact-b.md").exists()
    assert skill.read_text(encoding="utf-8") == before

