from agentkit.kernel.providers.types import Message
from agentkit.stores.session.sqlite import SQLiteSessionStore


def test_session_store_e2e_project_isolation_and_cross_project_search(tmp_path):
    db = tmp_path / "sessions.db"
    project_a = SQLiteSessionStore(db, project_id="project-a")
    project_b = SQLiteSessionStore(db, project_id="project-b")
    session_a = project_a.create_session(
        source="cli",
        model="mock",
        model_config={"type": "mock"},
        system_prompt="system-a",
        title="A",
    )
    session_b = project_b.create_session(
        source="server",
        model="mock",
        model_config={"type": "mock"},
        system_prompt="system-b",
        title="B",
    )
    project_a.add_message(session_a, Message(role="user", content="alpha recallneedle"))
    project_b.add_message(session_b, Message(role="user", content="beta recallneedle"))

    scoped_hits = project_a.search("recallneedle")
    cross_hits = project_a.search("recallneedle", cross_project=True)

    assert [hit.session_id for hit in scoped_hits] == [session_a]
    assert {hit.session_id for hit in cross_hits} == {session_a, session_b}
    assert all(">>>recallneedle<<<" in hit.snippet for hit in cross_hits)

