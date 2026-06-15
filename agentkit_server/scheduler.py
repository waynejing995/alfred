from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from agentkit.kernel.events.bus import EventBus
from agentkit.kernel.events.defs import JobDue, Tick


@dataclass(frozen=True)
class ScheduledJob:
    id: str
    due_at: float
    callback: Callable[[], Awaitable[None]]


class Scheduler:
    def __init__(self, bus: EventBus | None = None) -> None:
        self.bus = bus or EventBus()

    async def run_once(self, job: ScheduledJob) -> None:
        delay = max(0.0, job.due_at - time.time())
        if delay:
            await asyncio.sleep(delay)
        await self.bus.emit(Tick(source="scheduler"))
        await self.bus.emit(JobDue(job_id=job.id))
        await job.callback()

