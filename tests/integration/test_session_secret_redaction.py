import json

from agentkit import Agent
from agentkit.stores.session.sqlite import SQLiteSessionStore


def test_agent_session_store_redacts_model_headers(tmp_path):
    db = tmp_path / "sessions.db"
    store = SQLiteSessionStore(db, project_id="p")
    agent = Agent(
        config={
            "model": {
                "type": "mock",
                "params": {"http_headers": {"Authorization": "secret"}},
            }
        },
        session_store=store,
    )

    agent.run_sync("hello")
    row = store._db.execute("SELECT model_config FROM sessions").fetchone()

    assert json.loads(row["model_config"])["params"]["http_headers"] == "<redacted>"
