from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from agentkit.stores.memory.files import FilesMemoryProvider


class DreamEngine:
    def __init__(self, *, memory: FilesMemoryProvider) -> None:
        self.memory = memory

    def run_once(self) -> dict[str, int]:
        archive_dir = self.memory.facts_dir / ".archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        seen: dict[str, Path] = {}
        archived = 0
        for path in sorted(self.memory.facts_dir.glob("*.md")):
            digest = _digest(path)
            if digest in seen:
                shutil.move(str(path), archive_dir / path.name)
                archived += 1
            else:
                seen[digest] = path
        if archived:
            self.memory._rebuild_index()
        return {"archived": archived}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

