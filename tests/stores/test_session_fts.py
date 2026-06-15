from agentkit.kernel.providers.types import Message
from agentkit.stores.session.sqlite import SQLiteSessionStore


def test_session_search_returns_ranked_hits_with_context(tmp_path):
    store = SQLiteSessionStore(tmp_path / "sessions.db", project_id="project-a")
    session_id = store.create_session(
        source="cli",
        model="mock",
        model_config={},
        system_prompt="system",
        title="Searchable",
    )
    store.add_message(session_id, Message(role="user", content="before context"))
    store.add_message(session_id, Message(role="assistant", content="needle appears here"))
    store.add_message(session_id, Message(role="user", content="after context"))

    hits = store.search("needle", context_radius=1)

    assert len(hits) == 1
    assert hits[0].session_id == session_id
    assert ">>>needle<<<" in hits[0].snippet
    assert hits[0].session_title == "Searchable"
    assert [message.content for message in hits[0].context] == [
        "before context",
        "needle appears here",
        "after context",
    ]

