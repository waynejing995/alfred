from __future__ import annotations

import sys

import click
from loguru import logger

from agentkit import Agent
from agentkit_cli.output import OutputFormat, render_result


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
def chat(prompt: str, output_format: OutputFormat, verbose: bool) -> None:
    _configure_logging(verbose)
    agent = Agent()
    result = agent.run_sync(prompt)
    rendered = render_result(result, output_format=output_format, events=agent.last_events)
    click.echo(rendered)


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


if __name__ == "__main__":
    main()
