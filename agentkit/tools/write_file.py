from __future__ import annotations

from pathlib import Path


def write_file(path: str, content: str) -> dict[str, int | str]:
    target = Path(path)
    target.write_text(content)
    return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}
