from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import SkillRef, StepRecord


def test_trace_store_writes_trajectory_turn_step_and_replay_set(tmp_path):
    store = SQLiteTraceStore(tmp_path / "trace.db", project_id="project-a")
    trace_id = store.start_trace(
        session_id="session-1",
        task="read a file",
        active_skills=[SkillRef(name="file-reader", version="v1")],
    )
    store.record_turn(trace_id=trace_id, turn_id="turn-1", assistant_msg_id=7)
    store.append_step(
        StepRecord(
            step_id="step-1",
            trace_id=trace_id,
            turn_id="turn-1",
            seq=1,
            tool_name="hashread",
            tool_args={"path": "README.md"},
            tool_result="ok",
            result_status="ok",
            msg_id=8,
        )
    )
    store.mark_skill_used(trace_id, SkillRef(name="file-reader", version="v1"))
    store.seal_trace(
        trace_id,
        outcome="success",
        outcome_source="verifier",
        score=1.0,
        feedback="passed",
        budget_used=1,
    )

    trace = store.get_trace(trace_id)
    replay = store.replay_set("file-reader", min_outcome_quality=0.5)

    assert trace.session_id == "session-1"
    assert trace.steps[0].tool_name == "hashread"
    assert trace.used_skills == [SkillRef(name="file-reader", version="v1")]
    assert trace.sealed is True
    assert replay[0].trace_id == trace_id


def test_failure_and_success_sets_are_project_scoped(tmp_path):
    db = tmp_path / "trace.db"
    project_a = SQLiteTraceStore(db, project_id="project-a")
    project_b = SQLiteTraceStore(db, project_id="project-b")
    success = project_a.start_trace(
        session_id="session-a",
        task="success",
        active_skills=[SkillRef(name="skill", version="v1")],
    )
    failure = project_a.start_trace(
        session_id="session-b",
        task="failure",
        active_skills=[SkillRef(name="skill", version="v1")],
    )
    other = project_b.start_trace(
        session_id="session-c",
        task="other",
        active_skills=[SkillRef(name="skill", version="v1")],
    )
    project_a.seal_trace(success, outcome="success", outcome_source="verifier", score=1.0)
    project_a.seal_trace(failure, outcome="failure", outcome_source="verifier", score=0.0)
    project_b.seal_trace(other, outcome="success", outcome_source="verifier", score=1.0)

    assert [trace.trace_id for trace in project_a.success_set("skill")] == [success]
    assert [trace.trace_id for trace in project_a.failure_set("skill")] == [failure]

