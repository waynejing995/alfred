from __future__ import annotations

from pathlib import Path

from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import SkillUsed
from agentkit.stores.skill.loader import Catalog


def skills_list(catalog: Catalog) -> list[dict[str, str]]:
    return catalog.l0()


async def skill_view(
    catalog: Catalog,
    name: str,
    path: str | None = None,
    *,
    bus: EventBus | None = None,
    session_id: str = "",
) -> str:
    skill = catalog.get(name)
    if path is None:
        if bus is not None:
            await bus.emit(SkillUsed(skill_name=name, session_id=session_id))
        return skill.skill_md.read_text(encoding="utf-8")
    target = _validate_l2_path(skill.directory, path)
    return target.read_text(encoding="utf-8")


def _validate_l2_path(skill_dir: Path, path: str) -> Path:
    rel = Path(path)
    if rel.is_absolute() or any(part.startswith(".") for part in rel.parts):
        raise ValueError("skill_view path must be a non-dot relative path")
    target = (skill_dir / rel).resolve()
    root = skill_dir.resolve()
    if root not in target.parents:
        raise ValueError("skill_view path escaped skill directory")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(path)
    return target

