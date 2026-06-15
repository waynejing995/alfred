from agentkit_server.cron import CronRunner


async def test_cron_e2e_runs_unattended_and_writes_output(tmp_path):
    path = await CronRunner(tmp_path / "cron" / "output").run_job(
        job_id="daily",
        prompt="cron prompt",
    )

    assert path.exists()
    assert path.parent.name == "daily"
    assert path.read_text(encoding="utf-8") == "mock: cron prompt"

