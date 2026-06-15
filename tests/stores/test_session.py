from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.stores.session.sqlite import SQLiteSessionStore


def test_session_create_add_get_and_latest(tmp_path):
    store = SQLiteSessionStore(tmp_path / "sessions.db", project_id="project-a")
    session_id = store.create_session(
        source="cli",
        model="mock",
        model_config={"type": "mock"},
        system_prompt="system",
        title="First",
    )

    user_id = store.add_message(session_id, Message(role="user", content="hello"))
    assistant_id = store.add_message(
        session_id,
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="call_1", name="hashread", arguments={"path": "x"})],
        ),
    )
    messages = store.get_messages(session_id)
    sessions = store.list_sessions()

    assert user_id < assistant_id
    assert store.latest_session(source="cli") == session_id
    assert sessions[0].message_count == 2
    assert sessions[0].tool_call_count == 1
    assert messages[0].content == "hello"
    assert messages[1].tool_calls[0].name == "hashread"


def test_parent_chain_rehydrates_parent_before_child(tmp_path):
    store = SQLiteSessionStore(tmp_path / "sessions.db", project_id="project-a")
    parent_id = store.create_session(
        source="cli",
        model="mock",
        model_config={},
        system_prompt="system",
    )
    child_id = store.create_session(
        source="cli",
        model="mock",
        model_config={},
        system_prompt="system",
        parent_session_id=parent_id,
    )
    store.add_message(parent_id, Message(role="user", content="parent"))
    store.add_message(child_id, Message(role="user", content="child"))

    assert [message.content for message in store.get_messages(child_id)] == ["parent", "child"]
    assert [message.content for message in store.get_messages(child_id, include_chain=False)] == [
        "child"
    ]


def test_end_session_sets_reason(tmp_path):
    store = SQLiteSessionStore(tmp_path / "sessions.db", project_id="project-a")
    session_id = store.create_session(
        source="cli",
        model="mock",
        model_config={},
        system_prompt="system",
    )

    store.end_session(session_id, reason="normal")

    assert store.list_sessions()[0].end_reason == "normal"

