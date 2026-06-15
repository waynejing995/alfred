from __future__ import annotations

from agentkit.subsystems.goal.store import GoalStore


def set_goal(store: GoalStore, *, thread_id: str, objective: str) -> dict:
    return store.set(thread_id, objective).model_dump(mode="json")


def view_goal(store: GoalStore, *, thread_id: str) -> dict | None:
    state = store.view(thread_id)
    return state.model_dump(mode="json") if state else None


def pause_goal(store: GoalStore, *, thread_id: str) -> dict:
    return store.pause(thread_id).model_dump(mode="json")


def resume_goal(store: GoalStore, *, thread_id: str) -> dict:
    return store.resume(thread_id).model_dump(mode="json")


def clear_goal(store: GoalStore, *, thread_id: str) -> dict:
    store.clear(thread_id)
    return {"status": "cleared"}

