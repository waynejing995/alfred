from __future__ import annotations

import sys

import click
import yaml
from loguru import logger

from agentkit import Agent
from agentkit.control.config import AgentConfig
from agentkit.stores.session.sqlite import SQLiteSessionStore
from agentkit.stores.trace.sqlite import SQLiteTraceStore
from agentkit_cli.output import OutputFormat, final_result_frame, render_result, render_stream_frame
from agentkit_eval import Experiment, run_experiment


@click.group()
def main() -> None:
    """Alfred command line interface."""


@main.group()
def eval() -> None:
    """Evaluation commands."""


@eval.command("run")
@click.argument("path", type=click.Path(exists=True, dir_okay=False))
def eval_run(path: str) -> None:
    with open(path, encoding="utf-8") as handle:
        experiment = Experiment.model_validate(yaml.safe_load(handle))
    click.echo(render_stream_frame(run_experiment(experiment)))


@main.command()
@click.argument("prompt")
@click.option(
    "--output-format",
    type=click.Choice(["text", "json", "stream-json"]),
    default="text",
    show_default=True,
)
@click.option("-v", "--verbose", is_flag=True, help="Show DEBUG logs on stderr.")
@click.option(
    "--provider",
    type=click.Choice(["mock", "litellm"]),
    default="mock",
    show_default=True,
)
@click.option("--model", help="Model id for --provider litellm.")
@click.option("--env-key", default="ANTHROPIC_API_KEY", show_default=True)
@click.option("--base-url", envvar="ANTHROPIC_BASE_URL")
@click.option("--max-tokens", type=int)
@click.option("--tool-choice", help="Force a provider tool choice, e.g. hashread.")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False))
@click.option("--session-db", type=click.Path(dir_okay=False), help="Path to sessions.db.")
@click.option("--trace-db", type=click.Path(dir_okay=False), help="Path to trace.db.")
@click.option("--continue", "continue_session", is_flag=True, help="Continue latest CLI session.")
def chat(
    prompt: str,
    output_format: OutputFormat,
    verbose: bool,
    provider: str,
    model: str | None,
    env_key: str,
    base_url: str | None,
    max_tokens: int | None,
    tool_choice: str | None,
    config_path: str | None,
    session_db: str | None,
    trace_db: str | None,
    continue_session: bool,
) -> None:
    _configure_logging(verbose)
    session_store = SQLiteSessionStore(session_db) if session_db else None
    trace_store = SQLiteTraceStore(trace_db) if trace_db else None
    resume_id = (
        session_store.latest_session(source="cli")
        if session_store and continue_session
        else None
    )
    agent = Agent(
        config=AgentConfig.from_yaml(config_path)
        if config_path
        else _config(provider, model, env_key, base_url, max_tokens),
        session_store=session_store,
        trace_store=trace_store,
        resume_session_id=resume_id,
    )
    if output_format == "stream-json":
        result = agent.run_sync(
            prompt,
            stream=True,
            event_sink=lambda frame: click.echo(render_stream_frame(frame)),
            tool_choice=tool_choice,
        )
        click.echo(render_stream_frame(final_result_frame(result)))
        return
    result = agent.run_sync(prompt, tool_choice=tool_choice)
    rendered = render_result(result, output_format=output_format, events=agent.last_events)
    click.echo(rendered)


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


def _config(
    provider: str,
    model: str | None,
    env_key: str,
    base_url: str | None,
    max_tokens: int | None,
) -> dict:
    if provider == "mock":
        return {"model": {"type": "mock"}}
    if not model:
        raise click.UsageError("--model is required when --provider litellm")
    extra = {}
    if max_tokens is not None:
        extra["max_tokens"] = max_tokens
    return {
        "model": {
            "type": "litellm",
            "params": {
                "model": model,
                "env_key": env_key,
                "base_url": base_url,
                "extra": extra,
            },
        }
    }


if __name__ == "__main__":
    main()
