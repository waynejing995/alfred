from __future__ import annotations

from pathlib import Path


def write_file(path: str, content: str, *, root: str | None = None) -> dict[str, int | str]:
    target = _resolve_target(path, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}


def _resolve_target(path: str, root: str | None) -> Path:
    target = Path(path).resolve()
    if root is None:
        return target
    root_path = Path(root).resolve()
    if root_path not in target.parents and target != root_path:
        raise ValueError(f"write_file target escapes root: {target}")
    return target
