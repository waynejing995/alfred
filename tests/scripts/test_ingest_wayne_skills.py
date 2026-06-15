import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ingest_wayne_skills",
    Path(__file__).resolve().parents[2] / "scripts" / "ingest_wayne_skills.py",
)
ingest_module = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(ingest_module)


def test_ingest_wayne_skills_copies_skill_dirs(tmp_path):
    source = tmp_path / "source"
    skill = source / "wayne-demo"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: wayne-demo\ndescription: Demo.\n---\nbody\n",
        encoding="utf-8",
    )

    copied = ingest_module.ingest_wayne_skills(source, tmp_path / "bundled")

    assert copied == 1
    assert (tmp_path / "bundled" / "wayne-demo" / "SKILL.md").exists()
