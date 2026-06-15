import time

from agentkit_server.scheduler import ScheduledJob, Scheduler


async def test_scheduler_emits_tick_and_job_due():
    scheduler = Scheduler()
    seen = []
    scheduler.bus.on("*", lambda event: seen.append(event.name))

    async def callback():
        seen.append("callback")

    await scheduler.run_once(ScheduledJob(id="job-1", due_at=time.time(), callback=callback))

    assert seen == ["tick", "job_due", "callback"]

