from __future__ import annotations

import os
from importlib.resources import files


def binary_path() -> str | None:
    candidate = files(__package__).joinpath("bin", "fff")
    path = str(candidate)
    if os.path.exists(path):
        return path
    return None


__all__ = ["binary_path"]
