from starlette.testclient import TestClient

from agentkit_server.app import EVENT_HUB, app
from agentkit_server.replay import parse_sse, replay_text


def test_server_turn_events_replay_matches_response():
    EVENT_HUB.clear()
    client = TestClient(app)

    response = client.post("/turn", json={"prompt": "hello"})
    sse = client.get("/events", params={"session_id": response.json()["session_id"]})
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


def test_server_events_are_scoped_by_session():
    EVENT_HUB.clear()
    client = TestClient(app)

    first = client.post("/turn", json={"prompt": "first"}).json()
    second = client.post("/turn", json={"prompt": "second"}).json()
    first_frames = parse_sse(client.get("/events", params={"session_id": first["session_id"]}).text)

    assert replay_text(first_frames) == "mock: first"
    assert first["session_id"] != second["session_id"]
