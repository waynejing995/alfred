from __future__ import annotations

from agentkit.kernel.permission import Permission, PermissionLayer, PermissionRule
from agentkit.stores.skill.loader import LoadedSkill


def permission_layer_for_skill(
    skill: LoadedSkill,
    *,
    known_tool_names: list[str],
) -> PermissionLayer:
    allowed = set(skill.frontmatter.allowed_tool_names())
    if not allowed:
        return PermissionLayer(name=f"skill:{skill.name}", rules={})
    rules = {"tool": [PermissionRule(pattern="*", permission=Permission.DENY)]}
    for tool in known_tool_names:
        if tool in allowed:
            rules["tool"].append(PermissionRule(pattern=tool, permission=Permission.ALLOW))
    return PermissionLayer(name=f"skill:{skill.name}", rules=rules)
