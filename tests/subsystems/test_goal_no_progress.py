from agentkit.control.autonomy import AutonomyGate
from agentkit.kernel.permission import Autonomy
from agentkit.subsystems.goal.detector import NoProgressDetector
from agentkit.subsystems.goal.driver import GoalDriver
from agentkit.subsystems.goal.store import GoalStore


def test_no_progress_detector_repeated_turns():
    detector = NoProgressDetector(repeat_limit=3)

    assert detector.observe("t", tool_fingerprints=[], assistant_text="same") is False
    assert detector.observe("t", tool_fingerprints=[], assistant_text="same") is False
    assert detector.observe("t", tool_fingerprints=[], assistant_text="same") is True


def test_goal_driver_sets_no_progress_on_repeated_state(tmp_path):
    store = GoalStore(tmp_path / "goals")
    store.set("thread-1", "finish task")
    driver = GoalDriver(
        store=store,
        gate=AutonomyGate(Autonomy.AUTO),
        detector=NoProgressDetector(repeat_limit=3),
    )

    assert driver.continuation_message("thread-1", assistant_text="same")
    assert driver.continuation_message("thread-1", assistant_text="same")
    assert driver.continuation_message("thread-1", assistant_text="same") is None

    state = store.view("thread-1")
    assert state.status == "no_progress"
    assert state.reason == "repeated state"


def test_goal_driver_sets_no_progress_on_max_self_continuations(tmp_path):
    store = GoalStore(tmp_path / "goals")
    store.set("thread-1", "finish task")
    driver = GoalDriver(
        store=store,
        gate=AutonomyGate(Autonomy.AUTO),
        max_self_continuations=1,
    )

    assert driver.continuation_message("thread-1", assistant_text="one")
    assert driver.continuation_message("thread-1", assistant_text="two") is None

    assert store.view("thread-1").reason == "max_self_continuations"

