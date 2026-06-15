from agentkit.kernel.providers.types import Message
from agentkit.stores.project import resolve_project_id
from agentkit.stores.session.sqlite import SQLiteSessionStore


def test_resolve_project_id_uses_git_root(tmp_path):
    repo = tmp_path / "repo"
    subdir = repo / "pkg"
    subdir.mkdir(parents=True)
    (repo / ".git").mkdir()

    assert resolve_project_id(subdir) == str(repo)


def test_project_scope_is_default_and_cross_project_is_explicit(tmp_path):
    db = tmp_path / "sessions.db"
    store_a = SQLiteSessionStore(db, project_id="project-a")
    store_b = SQLiteSessionStore(db, project_id="project-b")
    session_a = store_a.create_session(
        source="cli",
        model="mock",
        model_config={},
        system_prompt="system",
    )
    session_b = store_b.create_session(
        source="cli",
        model="mock",
        model_config={},
        system_prompt="system",
    )
    store_a.add_message(session_a, Message(role="user", content="sharedneedle in project a"))
    store_b.add_message(session_b, Message(role="user", content="sharedneedle in project b"))

    assert store_a.latest_session() == session_a
    assert store_b.latest_session() == session_b
    assert [hit.project_id for hit in store_a.search("sharedneedle")] == ["project-a"]

    cross_project = store_a.search("sharedneedle", cross_project=True)

    assert {hit.project_id for hit in cross_project} == {"project-a", "project-b"}

