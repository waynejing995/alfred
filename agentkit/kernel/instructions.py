from __future__ import annotations

import os
import subprocess
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field


class InstructionReadError(RuntimeError):
    """A declared instruction source could not be read."""


class InstructionLayer(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: Path
    char_count: int
    included: bool = True
    skipped: str | None = None
    content: str = ""


class ResolvedInstructions(BaseModel):
    model_config = ConfigDict(frozen=True)

    layers: list[InstructionLayer] = Field(default_factory=list)
    merged: str = ""
    over_cap: bool = False
    char_cap: int = 20_000

    def manifest(self) -> list[dict[str, object]]:
        return [
            {
                "path": str(layer.path),
                "char_count": layer.char_count,
                "included": layer.included,
                "skipped": layer.skipped,
            }
            for layer in self.layers
        ]


class InstructionResolver:
    def __init__(self, *, char_cap: int = 20_000, enabled: bool = True) -> None:
        self.char_cap = char_cap
        self.enabled = enabled

    def resolve(
        self,
        cwd: str | Path,
        alfred_home: str | Path | None = None,
        *,
        declared_paths: list[str | Path] | None = None,
    ) -> ResolvedInstructions:
        if not self.enabled:
            return ResolvedInstructions(char_cap=self.char_cap)
        cwd_path = Path(cwd).resolve()
        home_path = Path(alfred_home or os.environ.get("ALFRED_HOME", "~/.alfred")).expanduser()
        paths: list[Path] = []
        global_path = home_path / "AGENTS.md"
        if global_path.exists():
            paths.append(global_path)
        paths.extend(self._project_instruction_paths(cwd_path))
        for declared in declared_paths or []:
            path = Path(declared).expanduser()
            if path not in paths:
                paths.append(path)

        layers = [self._read_layer(path) for path in paths]
        if not layers:
            logger.debug("no Alfred instruction files found for {}", cwd_path)
        merged = "\n\n".join(layer.content for layer in layers if layer.included)
        over_cap = len(merged) > self.char_cap
        if over_cap:
            logger.warning(
                "resolved instructions are over cap: chars={} cap={} (content kept uncut)",
                len(merged),
                self.char_cap,
            )
        return ResolvedInstructions(
            layers=layers,
            merged=merged,
            over_cap=over_cap,
            char_cap=self.char_cap,
        )

    def _project_instruction_paths(self, cwd: Path) -> list[Path]:
        git_root = self._git_root(cwd)
        if git_root is None:
            candidates = [cwd]
        else:
            candidates = []
            current = cwd
            while True:
                candidates.append(current)
                if current == git_root:
                    break
                if current == current.parent or current == Path.home():
                    break
                current = current.parent
            candidates.reverse()
        paths = []
        for directory in candidates:
            agents = directory / "AGENTS.md"
            claude = directory / "CLAUDE.md"
            if agents.exists():
                paths.append(agents)
            elif claude.exists():
                paths.append(claude)
        return paths

    @staticmethod
    def _git_root(cwd: Path) -> Path | None:
        try:
            output = subprocess.check_output(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=cwd,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (subprocess.CalledProcessError, FileNotFoundError):
            current = cwd
            while True:
                if (current / ".git").exists():
                    return current
                if current == current.parent or current == Path.home():
                    return None
                current = current.parent
        return Path(output).resolve()

    @staticmethod
    def _read_layer(path: Path) -> InstructionLayer:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InstructionReadError(
                f"declared instruction source cannot be read: {path}"
            ) from exc
        except UnicodeDecodeError as exc:
            raise InstructionReadError(f"declared instruction source is not UTF-8: {path}") from exc
        return InstructionLayer(path=path, char_count=len(content), content=content)
