from __future__ import annotations

from agentkit.kernel.events.defs import PostTool
from agentkit.stores.trace.types import Annotation


def annotation_from_post_tool(event: PostTool, *, step_id: str) -> Annotation:
    kind = "success" if event.ok else "failure"
    return Annotation(
        kind=kind,
        source="auto",
        confidence=0.6,
        target="step",
        target_id=step_id,
        evidence=f"post_tool ok={event.ok} tool={event.tool_name}",
        detector="post_tool_status",
    )


def is_user_pushback(text: str) -> bool:
    lowered = text.lower()
    markers = ["not that", "that's wrong", "you misunderstood", "i didn't ask"]
    return any(marker in lowered for marker in markers)

