from __future__ import annotations

from pathlib import Path

import yaml

from agentkit.control.autonomy import validate_self_edit
from agentkit.control.config import AgentConfig


def edit_own_config(
    path: str,
    new_content: str,
    *,
    origin: str = "agent",
    root: str | None = None,
) -> dict[str, str]:
    target = _resolve_target(path, root)
    old = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else {}
    new = yaml.safe_load(new_content) or {}
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValueError("Agent config must be YAML mappings")
    validate_self_edit(old, new, origin=origin)
    AgentConfig.model_validate(new)
    tmp = target.with_suffix(f"{target.suffix}.tmp")
    tmp.write_text(new_content, encoding="utf-8")
    tmp.replace(target)
    return {"status": "ok", "path": str(target)}


def _resolve_target(path: str, root: str | None) -> Path:
    target = Path(path).resolve()
    if root is None:
        return target
    root_path = Path(root).resolve()
    if root_path not in target.parents and target != root_path:
        raise ValueError(f"config edit target escapes root: {target}")
    return target
