from __future__ import annotations

import time
from pathlib import Path

from agentkit import Agent


class CronRunner:
    def __init__(self, output_root: str | Path) -> None:
        self.output_root = Path(output_root)
        self.output_root.mkdir(parents=True, exist_ok=True)

    async def run_job(self, *, job_id: str, prompt: str) -> Path:
        agent = Agent()
        result = await agent.run(prompt)
        directory = self.output_root / job_id
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{int(time.time() * 1000)}.md"
        path.write_text(str(result.message.content or ""), encoding="utf-8")
        return path

