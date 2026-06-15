from __future__ import annotations

from abc import ABC, abstractmethod

from agentkit.stores.trace.types import Annotation, SkillRef, StepRecord, TraceRecord


class TraceStore(ABC):
    @abstractmethod
    def start_trace(
        self,
        *,
        session_id: str,
        task: str,
        agent_role: str = "main",
        parent_trace_id: str | None = None,
        active_skills: list[SkillRef] | None = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def append_step(self, step: StepRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    def record_turn(
        self,
        *,
        trace_id: str,
        turn_id: str,
        assistant_msg_id: int | None = None,
        turn_outcome: str | None = None,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def add_annotation(self, trace_id: str, annotation: Annotation) -> int:
        raise NotImplementedError

    @abstractmethod
    def mark_skill_used(self, trace_id: str, skill: SkillRef) -> None:
        raise NotImplementedError

    @abstractmethod
    def seal_trace(
        self,
        trace_id: str,
        *,
        outcome: str = "unknown",
        outcome_source: str = "none",
        score: float | None = None,
        feedback: str | None = None,
        budget_used: int = 0,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_trace(self, trace_id: str) -> TraceRecord:
        raise NotImplementedError

    @abstractmethod
    def replay_set(self, skill_name: str, *, min_outcome_quality: float = 0.0) -> list[TraceRecord]:
        raise NotImplementedError

    @abstractmethod
    def failure_set(self, skill_name: str) -> list[TraceRecord]:
        raise NotImplementedError

    @abstractmethod
    def success_set(self, skill_name: str) -> list[TraceRecord]:
        raise NotImplementedError

