from __future__ import annotations

import sys

import click
from loguru import logger

from agentkit import Agent
from agentkit_cli.output import OutputFormat, final_result_frame, render_result, render_stream_frame


@click.group()
def main() -> None:
    """Alfred command line interface."""


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
def chat(
    prompt: str,
    output_format: OutputFormat,
    verbose: bool,
    provider: str,
    model: str | None,
    env_key: str,
    base_url: str | None,
    max_tokens: int | None,
) -> None:
    _configure_logging(verbose)
    agent = Agent(config=_config(provider, model, env_key, base_url, max_tokens))
    if output_format == "stream-json":
        result = agent.run_sync(
            prompt,
            stream=True,
            event_sink=lambda frame: click.echo(render_stream_frame(frame)),
        )
        click.echo(render_stream_frame(final_result_frame(result)))
        return
    result = agent.run_sync(prompt)
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
