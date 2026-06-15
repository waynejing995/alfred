from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Proposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    loop: str
    kind: str
    payload: dict
    status: Literal["pending", "accepted", "rejected"] = "pending"
    created_at: float = Field(default_factory=time.time)


class ProposalStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write_all({})

    def hold(self, proposal: Proposal) -> str:
        data = self._read_all()
        data[proposal.id] = proposal.model_dump(mode="json")
        self._write_all(data)
        return proposal.id

    def decide(self, proposal_id: str, *, accept: bool) -> Proposal:
        data = self._read_all()
        if proposal_id not in data:
            raise KeyError(f"unknown proposal: {proposal_id}")
        proposal = Proposal.model_validate(data[proposal_id])
        if proposal.status != "pending":
            return proposal
        proposal.status = "accepted" if accept else "rejected"
        data[proposal_id] = proposal.model_dump(mode="json")
        self._write_all(data)
        return proposal

    def list_pending(self) -> list[Proposal]:
        return [
            Proposal.model_validate(item)
            for item in self._read_all().values()
            if item["status"] == "pending"
        ]

    def _read_all(self) -> dict:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write_all(self, data: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)

