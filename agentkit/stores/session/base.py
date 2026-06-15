from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from agentkit.kernel.providers.types import Message
from agentkit.stores.session.types import EndReason, SearchHit, SessionMeta, SessionSource


class SessionStore(ABC):
    @abstractmethod
    def create_session(
        self,
        *,
        source: SessionSource,
        model: str,
        model_config: dict[str, Any],
        system_prompt: str,
        parent_session_id: str | None = None,
        title: str | None = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def add_message(self, session_id: str, msg: Message) -> int:
        raise NotImplementedError

    @abstractmethod
    def get_messages(self, session_id: str, *, include_chain: bool = True) -> list[Message]:
        raise NotImplementedError

    @abstractmethod
    def latest_session(self, *, source: SessionSource | None = None) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def list_sessions(self, *, limit: int = 20) -> list[SessionMeta]:
        raise NotImplementedError

    @abstractmethod
    def search(self, query: str, *, limit: int = 5, context_radius: int = 5) -> list[SearchHit]:
        raise NotImplementedError

    @abstractmethod
    def end_session(self, session_id: str, *, reason: EndReason) -> None:
        raise NotImplementedError

