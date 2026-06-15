from agentkit.kernel.events.defs import PostTool
from agentkit.stores.trace.detectors import annotation_from_post_tool, is_user_pushback
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import Annotation


def test_annotations_are_append_only_and_keep_source_trust(tmp_path):
    store = SQLiteTraceStore(tmp_path / "trace.db", project_id="project-a")
    trace_id = store.start_trace(session_id="session-1", task="task")

    store.add_annotation(
        trace_id,
        Annotation(
            kind="failure",
            source="auto",
            confidence=0.4,
            target="trajectory",
            target_id=trace_id,
        ),
    )
    store.add_annotation(
        trace_id,
        Annotation(
            kind="success",
            source="verifier",
            confidence=1.0,
            target="trajectory",
            target_id=trace_id,
        ),
    )

    annotations = store.get_trace(trace_id).annotations

    assert [annotation.source for annotation in annotations] == ["auto", "verifier"]
    assert [annotation.kind for annotation in annotations] == ["failure", "success"]


def test_post_tool_detector_surfaces_success_and_failure():
    success = annotation_from_post_tool(
        PostTool(session_id="s", turn_id="t", tool_name="hashread", ok=True),
        step_id="step-1",
    )
    failure = annotation_from_post_tool(
        PostTool(session_id="s", turn_id="t", tool_name="bash", ok=False),
        step_id="step-2",
    )

    assert success.kind == "success"
    assert failure.kind == "failure"
    assert success.target == "step"


def test_pushback_detector_is_structural_and_cheap():
    assert is_user_pushback("No, that's wrong. I didn't ask for that.")
    assert not is_user_pushback("thanks, that works")

