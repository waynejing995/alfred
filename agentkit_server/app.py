from __future__ import annotations

import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from agentkit import Agent
from agentkit_cli.output import final_result_frame

EVENTS: list[dict] = []


async def turn(request: Request) -> JSONResponse:
    body = await request.json()
    prompt = str(body["prompt"])
    agent = Agent()
    result = await agent.run(prompt, stream=True, event_sink=EVENTS.append)
    result_frame = final_result_frame(result)
    EVENTS.append(result_frame)
    return JSONResponse(result_frame["payload"])


async def events(_request: Request) -> StreamingResponse:
    async def body():
        for frame in EVENTS:
            yield f"event: {frame['type']}\n"
            yield f"data: {json.dumps(frame['payload'])}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream")


app = Starlette(routes=[Route("/turn", turn, methods=["POST"]), Route("/events", events)])

