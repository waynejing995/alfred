from __future__ import annotations

import shlex
import subprocess
from typing import Any


def bash(command: str, timeout: float | None = None) -> dict[str, Any]:
    result = subprocess.run(
        shlex.split(command),
        shell=False,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }
