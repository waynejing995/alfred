import pytest

from agentkit.kernel.events.bus import EventBus
from agentkit.stores.skill.loader import SkillFilter, build_catalog
from agentkit.tools.skill import skill_view, skills_list


def write_skill(root, name, description="Use this skill.", body="Body", metadata=None):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    meta = "\n".join(f"  {key}: {value}" for key, value in (metadata or {}).items())
    metadata_block = f"metadata:\n{meta}\n" if metadata else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n{metadata_block}---\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


@pytest.mark.asyncio
async def test_skill_loader_l0_l1_l2_and_skill_used_event(tmp_path):
    root = tmp_path / "skills"
    skill_dir = write_skill(root, "demo-skill", body="Full skill body")
    (skill_dir / "references").mkdir()
    (skill_dir / "references" / "note.md").write_text("reference", encoding="utf-8")
    catalog = build_catalog([root])
    bus = EventBus()
    seen = []
    bus.on("skill_used", lambda event: seen.append(event.skill_name))

    assert skills_list(catalog) == [{"name": "demo-skill", "description": "Use this skill."}]
    assert "Full skill body" in await skill_view(catalog, "demo-skill", bus=bus, session_id="s")
    assert await skill_view(catalog, "demo-skill", "references/note.md") == "reference"
    assert seen == ["demo-skill"]


def test_skill_filter_disable_and_tags(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "keep-skill", metadata={"tags": "python useful"})
    write_skill(root, "drop-skill", metadata={"tags": "rust"})

    catalog = build_catalog(
        [root],
        SkillFilter(include_tags=["python"], disable=["drop-skill"]),
    )

    assert list(catalog.skills) == ["keep-skill"]


def test_allowed_tools_are_parsed(tmp_path):
    root = tmp_path / "skills"
    skill_dir = write_skill(root, "tool-skill")
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        text.replace(
            "description: Use this skill.\n",
            "description: Use this skill.\nallowed-tools: hashread bash\n",
        ),
        encoding="utf-8",
    )

    skill = build_catalog([root]).get("tool-skill")

    assert skill.frontmatter.allowed_tool_names() == ["hashread", "bash"]

