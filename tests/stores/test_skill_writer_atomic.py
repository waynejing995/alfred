import json

from agentkit.stores.skill.loader import build_catalog
from agentkit.stores.skill.writer import SkillStoreWriter


async def test_skill_writer_writes_active_file_and_manifest_last(tmp_path):
    writer = SkillStoreWriter(tmp_path / "skills")

    skill_dir = await writer.write_skill(
        name="new-skill",
        description="Use this new skill.",
        body="New body",
        origin="distill",
        allowed_tools=["hashread"],
    )

    manifest = json.loads((skill_dir / ".versions" / "manifest.json").read_text())
    skill = build_catalog([tmp_path / "skills"]).get("new-skill")

    assert manifest["active"] == "v1"
    assert skill.body == "New body"
    assert skill.frontmatter.metadata["origin"] == "distill"
    assert skill.frontmatter.allowed_tool_names() == ["hashread"]


async def test_skill_writer_archives_whole_dir_and_revert_restores(tmp_path):
    writer = SkillStoreWriter(tmp_path / "skills")
    await writer.write_skill(
        name="new-skill",
        description="Use this new skill.",
        body="Version one",
        origin="human",
    )
    skill_dir = tmp_path / "skills" / "new-skill"
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "note.md").write_text("reference v1", encoding="utf-8")

    await writer.write_skill(
        name="new-skill",
        description="Use this new skill.",
        body="Version two",
        origin="evolve",
    )
    await writer.revert(name="new-skill", version="v1")

    skill = build_catalog([tmp_path / "skills"]).get("new-skill")

    assert skill.body == "Version one"
    assert (skill_dir / "references" / "note.md").read_text(encoding="utf-8") == "reference v1"

