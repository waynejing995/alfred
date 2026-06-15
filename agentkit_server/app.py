from __future__ import annotations

import json
import uuid
from collections import defaultdict, deque

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from agentkit import Agent
from agentkit_cli.output import final_result_frame


class EventHub:
    def __init__(self, *, maxlen: int = 200) -> None:
        self._buffers: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=maxlen))

    def append(self, session_id: str, frame: dict) -> None:
        self._buffers[session_id].append(frame)

    def frames(self, session_id: str) -> list[dict]:
        return list(self._buffers.get(session_id, []))

    def clear(self) -> None:
        self._buffers.clear()


EVENT_HUB = EventHub()


async def turn(request: Request) -> JSONResponse:
    body = await request.json()
    prompt = str(body["prompt"])
    session_id = str(body.get("session_id") or uuid.uuid4())
    agent = Agent()
    result = await agent.run(
        prompt,
        stream=True,
        event_sink=lambda frame: EVENT_HUB.append(session_id, frame),
    )
    result_frame = final_result_frame(result)
    EVENT_HUB.append(session_id, result_frame)
    return JSONResponse({"session_id": session_id, **result_frame["payload"]})


async def events(request: Request) -> StreamingResponse:
    session_id = request.query_params.get("session_id")
    if not session_id:
        return JSONResponse({"error": "session_id is required"}, status_code=400)

    async def body():
        for frame in EVENT_HUB.frames(session_id):
            yield f"event: {frame['type']}\n"
            yield f"data: {json.dumps(frame['payload'])}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream")


app = Starlette(routes=[Route("/turn", turn, methods=["POST"]), Route("/events", events)])
