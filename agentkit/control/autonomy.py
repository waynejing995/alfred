from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import AutonomyChanged
from agentkit.kernel.permission import Autonomy


class AutonomyGate:
    def __init__(self, initial: Autonomy = Autonomy.ASSIST, bus: EventBus | None = None) -> None:
        self._level = initial
        self._bus = bus or EventBus()

    @property
    def level(self) -> Autonomy:
        return self._level

    async def set(self, level: Autonomy, *, source: str) -> None:
        if level == self._level:
            return
        old = self._level
        self._level = level
        await self._bus.emit(AutonomyChanged(old=old.value, new=level.value, source=source))

    def allows_auto(self) -> bool:
        return self._level is not Autonomy.OFF

    def requires_confirm(self) -> bool:
        return self._level is Autonomy.ASSIST


class AutoLoop:
    def __init__(self, *, gate: AutonomyGate) -> None:
        if gate is None:
            raise TypeError("auto-loop requires AutonomyGate")
        self.gate = gate


class SelfEditForbidden(RuntimeError):
    pass


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evolve_merge: str = "confirm-required"
    distill_new_skill: str = "confirm-required"


def must_confirm(gate: AutonomyGate, per_loop_gate: str) -> bool:
    return gate.requires_confirm() or per_loop_gate == "confirm-required"


def validate_self_edit(old: dict, new: dict, *, origin: str) -> None:
    if origin != "agent":
        return
    for field in ["autonomy", "gates"]:
        if old.get(field) != new.get(field):
            raise SelfEditForbidden(
                f"agent may not modify '{field}' (e-stop integrity)"
            )

