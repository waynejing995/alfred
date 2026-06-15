from __future__ import annotations

import json


def parse_sse(text: str) -> list[dict]:
    frames = []
    event_type = None
    data_lines = []
    for line in text.splitlines():
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].strip())
        elif not line and event_type is not None:
            frames.append({"type": event_type, "payload": json.loads("\n".join(data_lines))})
            event_type = None
            data_lines = []
    return frames


def replay_text(frames: list[dict]) -> str:
    for frame in reversed(frames):
        if frame["type"] == "result":
            return str(frame["payload"].get("final_message", ""))
    return ""

