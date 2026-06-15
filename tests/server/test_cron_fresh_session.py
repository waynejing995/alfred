from agentkit_server.cron import CronRunner


async def test_cron_runner_writes_output_files_with_fresh_agents(tmp_path):
    runner = CronRunner(tmp_path / "cron" / "output")

    first = await runner.run_job(job_id="daily", prompt="first")
    second = await runner.run_job(job_id="daily", prompt="second")

    assert first.read_text(encoding="utf-8") == "mock: first"
    assert second.read_text(encoding="utf-8") == "mock: second"
    assert first != second

