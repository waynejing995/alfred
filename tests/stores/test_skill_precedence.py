from agentkit.stores.skill.loader import build_catalog


def write_skill(root, name, body):
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use this skill.\n---\n{body}\n",
        encoding="utf-8",
    )
    return skill_dir


def test_same_name_skill_higher_root_wins_and_shadow_recorded(tmp_path):
    high = tmp_path / "high"
    low = tmp_path / "low"
    write_skill(high, "demo-skill", "high")
    write_skill(low, "demo-skill", "low")

    catalog = build_catalog([high, low])

    assert catalog.get("demo-skill").body == "high"
    assert catalog.shadowed[0][0] == "demo-skill"


def test_versions_dir_is_invisible_to_loader(tmp_path):
    root = tmp_path / "skills"
    skill_dir = write_skill(root, "demo-skill", "current")
    archived = skill_dir / ".versions" / "v1" / "archived-skill"
    archived.mkdir(parents=True)
    (archived / "SKILL.md").write_text(
        "---\nname: archived-skill\ndescription: Old skill.\n---\nold\n",
        encoding="utf-8",
    )

    catalog = build_catalog([root])

    assert list(catalog.skills) == ["demo-skill"]


def test_corrupt_skill_is_warning_skip_not_catalog_crash(tmp_path):
    root = tmp_path / "skills"
    write_skill(root, "good-skill", "good")
    bad = root / "bad-skill"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: nope\n", encoding="utf-8")

    catalog = build_catalog([root])

    assert list(catalog.skills) == ["good-skill"]
    assert catalog.errors[0][0].endswith("bad-skill")

