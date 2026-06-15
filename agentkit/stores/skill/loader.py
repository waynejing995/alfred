from __future__ import annotations

from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from agentkit.stores.skill.frontmatter import SkillFrontmatter


class SkillCatalogError(Exception):
    pass


class SkillFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_tags: list[str] = Field(default_factory=list)
    exclude_tags: list[str] = Field(default_factory=list)
    disable: list[str] = Field(default_factory=list)


class LoadedSkill(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    name: str
    description: str
    root: Path
    directory: Path
    skill_md: Path
    frontmatter: SkillFrontmatter
    body: str

    def l0_card(self) -> dict[str, str]:
        return {"name": self.name, "description": self.description}


class Catalog(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    skills: dict[str, LoadedSkill] = Field(default_factory=dict)
    errors: list[tuple[str, str]] = Field(default_factory=list)
    shadowed: list[tuple[str, str, str]] = Field(default_factory=list)

    def l0(self) -> list[dict[str, str]]:
        return [self.skills[name].l0_card() for name in sorted(self.skills)]

    def l0_text(self) -> str:
        return "\n".join(f"- {card['name']}: {card['description']}" for card in self.l0())

    def get(self, name: str) -> LoadedSkill:
        try:
            return self.skills[name]
        except KeyError as exc:
            raise KeyError(f"unknown skill: {name}") from exc


def build_catalog(roots: list[str | Path], filt: SkillFilter | None = None) -> Catalog:
    seen: dict[str, LoadedSkill] = {}
    errors: list[tuple[str, str]] = []
    shadowed: list[tuple[str, str, str]] = []
    for root_like in roots:
        root = Path(root_like).expanduser()
        if not root.exists():
            logger.debug("skill root absent, skipping: {}", root)
            continue
        for skill_dir in _iter_skill_dirs(root):
            try:
                skill = load_skill(skill_dir, root=root)
            except SkillCatalogError as exc:
                logger.warning("skipping corrupt skill {}: {}", skill_dir, exc)
                errors.append((str(skill_dir), str(exc)))
                continue
            if skill.name in seen:
                shadowed.append((skill.name, str(skill_dir), str(seen[skill.name].directory)))
                logger.warning(
                    "skill '{}' in {} shadowed by higher-precedence {}",
                    skill.name,
                    skill_dir,
                    seen[skill.name].directory,
                )
                continue
            seen[skill.name] = skill
    filtered = _apply_filter(seen, filt or SkillFilter())
    return Catalog(skills=filtered, errors=errors, shadowed=shadowed)


def load_skill(skill_dir: str | Path, *, root: str | Path | None = None) -> LoadedSkill:
    directory = Path(skill_dir)
    skill_md = directory / "SKILL.md"
    if not skill_md.exists():
        raise SkillCatalogError("missing SKILL.md")
    try:
        raw = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillCatalogError(f"unreadable SKILL.md: {exc}") from exc
    frontmatter_raw, body = _split_frontmatter(raw)
    try:
        data = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as exc:
        raise SkillCatalogError(f"bad YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillCatalogError("frontmatter is not a mapping")
    try:
        frontmatter = SkillFrontmatter.model_validate(data)
    except Exception as exc:
        raise SkillCatalogError(f"invalid frontmatter: {exc}") from exc
    if frontmatter.name != directory.name:
        raise SkillCatalogError(f"name '{frontmatter.name}' != dir '{directory.name}'")
    return LoadedSkill(
        name=frontmatter.name,
        description=frontmatter.description,
        root=Path(root or directory.parent),
        directory=directory,
        skill_md=skill_md,
        frontmatter=frontmatter,
        body=body.strip(),
    )


def _iter_skill_dirs(root: Path) -> list[Path]:
    return sorted(
        child
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".") and (child / "SKILL.md").exists()
    )


def _split_frontmatter(raw: str) -> tuple[str, str]:
    if not raw.startswith("---\n"):
        raise SkillCatalogError("missing frontmatter fence")
    try:
        _empty, rest = raw.split("---\n", 1)
        frontmatter, body = rest.split("---\n", 1)
    except ValueError as exc:
        raise SkillCatalogError("malformed frontmatter fence") from exc
    return frontmatter, body


def _apply_filter(skills: dict[str, LoadedSkill], filt: SkillFilter) -> dict[str, LoadedSkill]:
    include = {tag.lower() for tag in filt.include_tags}
    exclude = {tag.lower() for tag in filt.exclude_tags}
    disabled = set(filt.disable)
    output: dict[str, LoadedSkill] = {}
    for name, skill in skills.items():
        if name in disabled:
            logger.info("skill '{}' disabled by skill_filter.disable", name)
            continue
        tags = skill.frontmatter.tags()
        if include and not (tags & include):
            logger.debug("skill '{}' excluded: no include_tag match", name)
            continue
        if tags & exclude:
            logger.debug("skill '{}' excluded by exclude_tags", name)
            continue
        output[name] = skill
    return output

