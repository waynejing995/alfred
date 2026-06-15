import json

from agentkit.kernel.budget import IterationBudget
from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.loop import TurnCtx, run_turn
from agentkit.kernel.providers.mock import MockProvider
from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.kernel.registries import ToolsRegistry
from agentkit.stores.trace.recorder import TraceRecorder
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit.stores.trace.types import SkillRef, StepRecord


def test_trace_store_e2e_jsonl_seal_once_and_replay_set(tmp_path):
    store = SQLiteTraceStore(
        tmp_path / "trace.db",
        traces_dir=tmp_path / "traces",
        project_id="project-a",
    )
    trace_id = store.start_trace(
        session_id="session-1",
        task="read file",
        active_skills=[SkillRef(name="reader", version="v1")],
    )
    store.record_turn(trace_id=trace_id, turn_id="turn-1", assistant_msg_id=42)
    store.append_step(
        StepRecord(
            step_id="step-1",
            trace_id=trace_id,
            turn_id="turn-1",
            seq=1,
            tool_name="hashread",
            tool_args={"path": "README.md"},
            tool_result="README",
            result_status="ok",
            msg_id=43,
        )
    )

    store.seal_trace(trace_id, outcome="success", outcome_source="verifier", score=0.9)
    store.seal_trace(trace_id, outcome="failure", outcome_source="auto", score=0.0)

    trace = store.get_trace(trace_id)
    records = [
        json.loads(line)
        for line in open(trace.body_path, encoding="utf-8").read().splitlines()
    ]
    replay = store.replay_set("reader", min_outcome_quality=0.5)

    assert sum(record.get("kind") == "seal" for record in records) == 1
    assert replay[0].trace_id == trace_id
    assert replay[0].steps[0].tool_args == {"path": "README.md"}


async def test_trace_store_e2e_records_real_loop_tool_events(tmp_path):
    store = SQLiteTraceStore(tmp_path / "trace.db", traces_dir=tmp_path / "traces", project_id="p")
    bus = EventBus()
    recorder = TraceRecorder(store, session_id="session-1", task="echo task")
    recorder.attach(bus)
    tools = ToolsRegistry()
    tools.register(
        name="echo",
        description="Echo input",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=lambda text: text,
        permission_bucket="read",
    )
    provider = MockProvider(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[ToolCall(id="call_1", name="echo", arguments={"text": "hi"})],
            ),
            "done",
        ]
    )

    await run_turn(
        TurnCtx(provider=provider, tools=tools, budget=IterationBudget(3), bus=bus),
        "hello",
    )
    store.seal_trace(recorder.trace_id, outcome="success", outcome_source="verifier", score=1.0)
    trace = store.get_trace(recorder.trace_id)

    assert trace.steps[0].tool_name == "echo"
    assert trace.steps[0].tool_args == {"text": "hi"}
    assert trace.annotations[0].kind == "success"
