from agentkit.control.autonomy import AutonomyGate
from agentkit.kernel.permission import Autonomy
from agentkit.subsystems.goal.detector import NoProgressDetector
from agentkit.subsystems.goal.driver import GoalDriver
from agentkit.subsystems.goal.store import GoalStore


def test_goal_driver_e2e_unsatisfiable_goal_halts_with_no_progress(tmp_path):
    store = GoalStore(tmp_path / "goals")
    store.set("thread-1", "solve impossible task")
    driver = GoalDriver(
        store=store,
        gate=AutonomyGate(Autonomy.AUTO),
        detector=NoProgressDetector(repeat_limit=3),
    )

    messages = [
        driver.continuation_message("thread-1", assistant_text="still trying"),
        driver.continuation_message("thread-1", assistant_text="still trying"),
        driver.continuation_message("thread-1", assistant_text="still trying"),
    ]

    assert messages[:2] == [
        "continue toward: solve impossible task",
        "continue toward: solve impossible task",
    ]
    assert messages[2] is None
    assert store.view("thread-1").status == "no_progress"

