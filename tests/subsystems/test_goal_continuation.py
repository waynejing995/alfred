from agentkit.control.autonomy import AutonomyGate
from agentkit.kernel.permission import Autonomy
from agentkit.subsystems.goal.driver import GoalDriver
from agentkit.subsystems.goal.store import GoalStore


def test_goal_driver_self_continues_when_auto_and_active(tmp_path):
    store = GoalStore(tmp_path / "goals")
    store.set("thread-1", "finish task")
    driver = GoalDriver(store=store, gate=AutonomyGate(Autonomy.AUTO))

    message = driver.continuation_message("thread-1", assistant_text="working")

    assert message == "continue toward: finish task"
    assert store.view("thread-1").self_continuations == 1


def test_goal_driver_halts_when_autonomy_off_or_assist(tmp_path):
    store = GoalStore(tmp_path / "goals")
    store.set("thread-1", "finish task")

    assert (
        GoalDriver(store=store, gate=AutonomyGate(Autonomy.OFF)).continuation_message(
            "thread-1",
            assistant_text="working",
        )
        is None
    )
    assert (
        GoalDriver(store=store, gate=AutonomyGate(Autonomy.ASSIST)).continuation_message(
            "thread-1",
            assistant_text="working",
        )
        is None
    )


def test_goal_store_verbs_persist(tmp_path):
    store = GoalStore(tmp_path / "goals")
    store.set("thread-1", "finish task")
    store.pause("thread-1")
    assert store.view("thread-1").status == "paused"
    store.resume("thread-1")
    assert store.view("thread-1").status == "active"
    store.complete("thread-1")
    assert store.view("thread-1").status == "complete"
    store.clear("thread-1")
    assert store.view("thread-1") is None

