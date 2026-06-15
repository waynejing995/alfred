import json

from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import StepRecord


def test_seal_trace_writes_terminal_record_once(tmp_path):
    store = SQLiteTraceStore(tmp_path / "trace.db", project_id="project-a")
    trace_id = store.start_trace(session_id="session-1", task="task")
    store.append_step(
        StepRecord(
            step_id="step-1",
            trace_id=trace_id,
            seq=1,
            tool_name="hashread",
            tool_args={"path": "README.md"},
        )
    )

    store.seal_trace(trace_id, outcome="success", outcome_source="verifier", score=1.0)
    store.seal_trace(trace_id, outcome="failure", outcome_source="auto", score=0.0)

    trace = store.get_trace(trace_id)
    records = [
        json.loads(line)
        for line in trace.body_path and open(trace.body_path, encoding="utf-8").read().splitlines()
    ]

    assert trace.outcome == "success"
    assert trace.sealed is True
    assert sum(record.get("kind") == "seal" for record in records) == 1

