from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


class SkillStoreWriter:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def write_skill(
        self,
        *,
        name: str,
        description: str,
        body: str,
        origin: str,
        metadata: dict[str, str] | None = None,
        allowed_tools: list[str] | None = None,
    ) -> Path:
        async with self._locks[name]:
            skill_dir = self.root / name
            versions_dir = skill_dir / ".versions"
            versions_dir.mkdir(parents=True, exist_ok=True)
            manifest = self._read_manifest(skill_dir)
            version = f"v{len(manifest.get('history', [])) + 1}"
            if (skill_dir / "SKILL.md").exists():
                previous = manifest.get("active") or f"v{len(manifest.get('history', []))}"
                self._archive_skill(skill_dir, versions_dir / str(previous))
            else:
                skill_dir.mkdir(parents=True, exist_ok=True)
            frontmatter = {
                "name": name,
                "description": description,
                "metadata": {**(metadata or {}), "origin": origin},
            }
            if allowed_tools:
                frontmatter["allowed-tools"] = " ".join(allowed_tools)
            content = f"---\n{yaml.safe_dump(frontmatter, sort_keys=True)}---\n{body.strip()}\n"
            tmp = skill_dir / f".SKILL.md.{uuid.uuid4().hex}.tmp"
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, skill_dir / "SKILL.md")
            manifest.setdefault("history", []).append(
                {"version": version, "origin": origin, "ts": time.time()}
            )
            manifest["active"] = version
            self._write_manifest(skill_dir, manifest)
            return skill_dir

    async def revert(self, *, name: str, version: str) -> Path:
        async with self._locks[name]:
            skill_dir = self.root / name
            archived = skill_dir / ".versions" / version
            if not archived.exists():
                raise FileNotFoundError(f"missing archived skill version: {name}/{version}")
            for child in archived.iterdir():
                target = skill_dir / child.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                if child.is_dir():
                    shutil.copytree(child, target)
                else:
                    shutil.copy2(child, target)
            manifest = self._read_manifest(skill_dir)
            manifest["active"] = version
            manifest.setdefault("history", []).append(
                {"version": version, "origin": "revert", "ts": time.time()}
            )
            self._write_manifest(skill_dir, manifest)
            return skill_dir

    def _archive_skill(self, skill_dir: Path, archive_dir: Path) -> None:
        if archive_dir.exists():
            shutil.rmtree(archive_dir)
        archive_dir.mkdir(parents=True)
        for child in skill_dir.iterdir():
            if child.name == ".versions":
                continue
            target = archive_dir / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)

    def _read_manifest(self, skill_dir: Path) -> dict[str, Any]:
        path = skill_dir / ".versions" / "manifest.json"
        if not path.exists():
            return {"active": None, "history": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_manifest(self, skill_dir: Path, manifest: dict[str, Any]) -> None:
        path = skill_dir / ".versions" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".manifest.json.{uuid.uuid4().hex}.tmp")
        tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
