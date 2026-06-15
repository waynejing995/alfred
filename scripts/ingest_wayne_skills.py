from __future__ import annotations

import shutil
from pathlib import Path

import click
from loguru import logger


@click.command()
@click.argument("source_root", type=click.Path(exists=True, file_okay=False))
@click.argument("dest_root", type=click.Path(file_okay=False))
def main(source_root: str, dest_root: str) -> None:
    copied = ingest_wayne_skills(Path(source_root), Path(dest_root))
    logger.info("copied {} wayne skills into {}", copied, dest_root)


def ingest_wayne_skills(source_root: Path, dest_root: Path) -> int:
    dest_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    for skill_dir in sorted(source_root.glob("wayne-*")):
        if not (skill_dir / "SKILL.md").exists():
            continue
        target = dest_root / skill_dir.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(skill_dir, target)
        copied += 1
    return copied


if __name__ == "__main__":
    main()

