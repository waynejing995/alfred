from agentkit import Agent
from agentkit_server.cron import CronRunner


async def test_cron_runner_writes_output_files_with_fresh_agents(tmp_path):
    runner = CronRunner(tmp_path / "cron" / "output")

    first = await runner.run_job(job_id="daily", prompt="first")
    second = await runner.run_job(job_id="daily", prompt="second")

    assert first.read_text(encoding="utf-8") == "mock: first"
    assert second.read_text(encoding="utf-8") == "mock: second"
    assert first != second


async def test_cron_runner_rejects_unsafe_job_id(tmp_path):
    try:
        await CronRunner(tmp_path / "out").run_job(job_id="../bad", prompt="x")
    except ValueError as exc:
        assert "invalid cron job_id" in str(exc)
    else:
        raise AssertionError("unsafe job_id was not rejected")


async def test_cron_runner_accepts_agent_factory(tmp_path):
    runner = CronRunner(
        tmp_path / "out",
        agent_factory=lambda: Agent(config={"model": {"type": "mock"}}),
    )

    path = await runner.run_job(job_id="daily", prompt="factory")

    assert path.read_text(encoding="utf-8") == "mock: factory"
