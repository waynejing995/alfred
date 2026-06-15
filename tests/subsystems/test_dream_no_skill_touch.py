from agentkit.stores.memory.files import FilesMemoryProvider
from agentkit.subsystems.dream import DreamEngine


def test_dream_has_no_skill_store_dependency_and_does_not_touch_skill_files(tmp_path):
    memory = FilesMemoryProvider(tmp_path / "memory")
    skill = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo-skill\ndescription: Skill.\n---\nbody\n", encoding="utf-8")
    before = skill.read_text(encoding="utf-8")

    DreamEngine(memory=memory).run_once()

    assert skill.read_text(encoding="utf-8") == before

