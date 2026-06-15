from __future__ import annotations

import hashlib
from collections import deque


class NoProgressDetector:
    def __init__(self, *, window: int = 10, repeat_limit: int = 3) -> None:
        self.window = window
        self.repeat_limit = repeat_limit
        self._turns: dict[str, deque[str]] = {}

    def observe(self, thread_id: str, *, tool_fingerprints: list[str], assistant_text: str) -> bool:
        fingerprint = self._fingerprint(tool_fingerprints, assistant_text)
        turns = self._turns.setdefault(thread_id, deque(maxlen=self.window))
        turns.append(fingerprint)
        return self._repeated(turns) or self._ping_pong(turns)

    @staticmethod
    def tool_fingerprint(tool_name: str, serialized_args: str) -> str:
        return hashlib.sha256(f"{tool_name}:{serialized_args}".encode()).hexdigest()

    @staticmethod
    def _fingerprint(tool_fingerprints: list[str], assistant_text: str) -> str:
        body = "\n".join(sorted(tool_fingerprints)) + "\n" + assistant_text.strip()
        return hashlib.sha256(body.encode()).hexdigest()

    def _repeated(self, turns: deque[str]) -> bool:
        if len(turns) < self.repeat_limit:
            return False
        tail = list(turns)[-self.repeat_limit :]
        return len(set(tail)) == 1

    def _ping_pong(self, turns: deque[str]) -> bool:
        if len(turns) < 4:
            return False
        tail = list(turns)[-4:]
        return tail[0] == tail[2] and tail[1] == tail[3] and tail[0] != tail[1]

