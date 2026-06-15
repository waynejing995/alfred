from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger


def fff(query: str, path: str = ".", limit: int = 20) -> dict[str, Any]:
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(path)

    warnings: list[str] = []
    binary = _bundled_fff_binary()
    if binary is not None:
        try:
            return {"backend": "fff", "matches": _run_bundled_fff(binary, query, root, limit)}
        except Exception as exc:
            _warn_fallback(
                warnings,
                f"bundled fff failed ({type(exc).__name__}: {exc}); falling back",
            )
    else:
        _warn_fallback(warnings, "bundled fff unavailable; falling back")

    rg = _rg_path()
    if rg is not None:
        try:
            return {
                "backend": "rg",
                "matches": _run_rg(rg, query, root, limit),
                "warnings": warnings,
            }
        except Exception as exc:
            _warn_fallback(warnings, f"rg failed ({type(exc).__name__}: {exc}); falling back")
    else:
        _warn_fallback(warnings, "rg unavailable; falling back to pure-Python grep")

    return {"backend": "python", "matches": _python_grep(query, root, limit), "warnings": warnings}


def _bundled_fff_binary() -> str | None:
    try:
        from agentkit_fff_linux_x64 import binary_path
    except ImportError:
        return None
    candidate = binary_path()
    if candidate is None or not os.access(candidate, os.X_OK):
        return None
    return candidate


def _rg_path() -> str | None:
    return shutil.which("rg")


def _run_bundled_fff(binary: str, query: str, root: Path, limit: int) -> list[dict[str, Any]]:
    result = subprocess.run(
        [binary, query, str(root)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1}:
        raise RuntimeError(result.stderr.strip() or f"fff exited {result.returncode}")
    return [{"path": line} for line in result.stdout.splitlines()[:limit]]


def _run_rg(rg: str, query: str, root: Path, limit: int) -> list[dict[str, Any]]:
    result = subprocess.run(
        [rg, "--line-number", "--color", "never", "--fixed-strings", query, str(root)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 1:
        return []
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"rg exited {result.returncode}")
    return [_parse_rg_line(line) for line in result.stdout.splitlines()[:limit]]


def _python_grep(query: str, root: Path, limit: int) -> list[dict[str, Any]]:
    files = [root] if root.is_file() else sorted(path for path in root.rglob("*") if path.is_file())
    matches: list[dict[str, Any]] = []
    for file_path in files:
        try:
            lines = file_path.read_text(errors="replace").splitlines()
        except OSError as exc:
            raise RuntimeError(f"failed to read {file_path}: {exc}") from exc
        for line_number, line in enumerate(lines, start=1):
            if query in line:
                matches.append({"path": str(file_path), "line": line_number, "text": line})
                if len(matches) >= limit:
                    return matches
    return matches


def _parse_rg_line(line: str) -> dict[str, Any]:
    path, line_number, text = line.split(":", 2)
    return {"path": path, "line": int(line_number), "text": text}


def _warn_fallback(warnings: list[str], message: str) -> None:
    logger.warning("fff fallback: {}", message)
    warnings.append(message)
