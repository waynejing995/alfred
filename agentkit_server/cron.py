from __future__ import annotations

import re
import uuid
from pathlib import Path

from agentkit import Agent


class CronRunner:
    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)

    async def run_job(self, *, job_id: str, prompt: str) -> Path:
        if not re.fullmatch(r"[a-zA-Z0-9_.-]+", job_id):
            raise ValueError(f"invalid cron job_id: {job_id!r}")
        agent = Agent()
        result = await agent.run(prompt)
        directory = self.output_root / job_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid.uuid4().hex}.md"
        with path.open("x", encoding="utf-8") as handle:
            handle.write(str(result.message.content or ""))
        return path
