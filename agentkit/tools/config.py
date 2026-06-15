from __future__ import annotations

from pathlib import Path

import yaml

from agentkit.control.autonomy import validate_self_edit


def edit_own_config(path: str, new_content: str, *, origin: str = "agent") -> dict[str, str]:
    target = Path(path)
    old = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else {}
    new = yaml.safe_load(new_content) or {}
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError("Agent config must be YAML mappings")
    validate_self_edit(old, new, origin=origin)
    target.write_text(new_content, encoding="utf-8")
    return {"status": "ok", "path": str(target)}

