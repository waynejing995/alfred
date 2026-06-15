from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import click
from loguru import logger

ROOT = Path(__file__).resolve().parents[1]
COMPANION_DIR = ROOT / "agentkit_fff_linux_x64"
BIN_PATH = COMPANION_DIR / "bin" / "fff"


@click.command()
@click.option(
    "--source",
    type=click.Path(exists=True, dir_okay=False),
    help="Use an existing fff binary.",
)
@click.option("--npm-package", default="@ff-labs/fff-bun", show_default=True)
@click.option("-v", "--verbose", is_flag=True, help="Show debug logs.")
def main(source: str | None, npm_package: str, verbose: bool) -> None:
    configure_logging(verbose)
    if platform.system() != "Linux" or platform.machine() not in {"x86_64", "AMD64"}:
        raise click.ClickException("this companion package is linux-x64 only")
    BIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if source:
        install_existing(Path(source))
    else:
        install_from_npm(npm_package)
    validate_binary(BIN_PATH)
    logger.info("installed fff binary at {}", BIN_PATH)


def configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


def install_existing(source: Path) -> None:
    logger.info("copying fff binary from {}", source)
    shutil.copy2(source, BIN_PATH)
    make_executable(BIN_PATH)


def install_from_npm(package: str) -> None:
    npm = shutil.which("npm")
    if npm is None:
        raise click.ClickException("npm is required to install fff automatically; pass --source")
    logger.info("installing {} into a temporary npm prefix", package)
    tmp = ROOT / ".tmp-fff-install"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        subprocess.run([npm, "init", "-y"], cwd=tmp, check=True, capture_output=True, text=True)
        subprocess.run(
            [npm, "install", package],
            cwd=tmp,
            check=True,
            capture_output=True,
            text=True,
        )
        candidates = [
            path
            for path in (tmp / "node_modules").rglob("*")
            if path.is_file() and path.name in {"fff", "fff-mcp"}
        ]
        if not candidates:
            raise click.ClickException(
                f"installed {package}, but no fff/fff-mcp executable was found"
            )
        # Prefer plain fff if upstream exposes one; otherwise keep fff-mcp for inspection.
        source = sorted(candidates, key=lambda path: (path.name != "fff", len(path.parts)))[0]
        logger.info("found upstream executable candidate {}", source)
        shutil.copy2(source, BIN_PATH)
        make_executable(BIN_PATH)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def validate_binary(path: Path) -> None:
    if not path.exists():
        raise click.ClickException(f"fff binary missing after install: {path}")
    if not os.access(path, os.X_OK):
        raise click.ClickException(f"fff binary is not executable: {path}")
    result = subprocess.run([str(path), "--help"], check=False, capture_output=True, text=True)
    if result.returncode not in {0, 1}:
        raise click.ClickException(
            f"fff binary did not respond to --help: rc={result.returncode} stderr={result.stderr}"
        )


def make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


if __name__ == "__main__":
    main()
