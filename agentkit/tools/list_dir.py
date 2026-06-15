from __future__ import annotations

from pathlib import Path
from typing import Any


def list_dir(path: str = ".") -> list[dict[str, Any]]:
    root = Path(path)
    entries = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        entries.append(
            {
                "name": entry.name,
                "path": str(entry),
                "type": "dir" if entry.is_dir() else "file",
            }
        )
    return entries
