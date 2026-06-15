from agentkit_server.replay import parse_sse, replay_text


def test_replay_parses_sse_and_returns_result_text():
    sse = 'event: result\ndata: {"final_message": "hello"}\n\n'

    assert replay_text(parse_sse(sse)) == "hello"

