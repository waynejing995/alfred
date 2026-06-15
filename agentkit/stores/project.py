from __future__ import annotations

import subprocess
from pathlib import Path


def resolve_project_id(cwd: str | Path) -> str:
    """Resolve Alfred's project id using the same git-root walk as instructions."""
    path = Path(cwd).resolve()
    root = _git_root(path)
    return str(root or path)


def _git_root(cwd: Path) -> Path | None:
    try:
        output = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        current = cwd
        while True:
            if (current / ".git").exists():
                return current
            if current == current.parent or current == Path.home():
                return None
            current = current.parent
    return Path(output).resolve()

