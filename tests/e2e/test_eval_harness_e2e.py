import json
import subprocess
from pathlib import Path


def test_eval_harness_e2e_cli_run_outputs_scores_and_cost(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    experiment = tmp_path / "experiment.yaml"
    experiment.write_text(
        """
name: smoke
arms:
  - name: baseline
    config:
      model:
        type: mock
  - name: same
    config:
      model:
        type: mock
    varies: []
tasks:
  - id: t1
    prompt: hello
    expected: "mock: hello"
""",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["uv", "run", "alfred", "eval", "run", str(experiment)],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)

    assert payload["arms"]["baseline"]["score"]["pass_rate"] == 1.0
    assert payload["arms"]["baseline"]["cost_tokens"] > 0

