import socket
import subprocess
import time

import httpx

from agentkit_server.replay import parse_sse, replay_text


def test_server_sse_replay_e2e_matches_turn_response():
    port = _free_port()
    proc = subprocess.Popen(
        [
            "uv",
            "run",
            "uvicorn",
            "agentkit_server.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_ready(port)
        response = httpx.post(f"http://127.0.0.1:{port}/turn", json={"prompt": "hello"})
        sse = httpx.get(f"http://127.0.0.1:{port}/events")
        frames = parse_sse(sse.text)

        assert response.json()["final_message"] == "mock: hello"
        assert replay_text(frames) == "mock: hello"
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _wait_ready(port: int) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            httpx.get(f"http://127.0.0.1:{port}/events", timeout=0.2)
            return
        except Exception:
            time.sleep(0.05)
    raise RuntimeError("server did not become ready")

