from starlette.testclient import TestClient

from agentkit_server.app import EVENTS, app
from agentkit_server.replay import parse_sse, replay_text


def test_server_turn_events_replay_matches_response():
    EVENTS.clear()
    client = TestClient(app)

    response = client.post("/turn", json={"prompt": "hello"})
    sse = client.get("/events")
    frames = parse_sse(sse.text)

    assert response.json()["final_message"] == "mock: hello"
    assert replay_text(frames) == response.json()["final_message"]
    assert [frame["type"] for frame in frames] == [
        "session_start",
        "turn_start",
        "stream_delta",
        "turn_end",
        "result",
    ]

