from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import Any

# 32-bit anchor. Threat model is "did the model read a stale version of this
# line", not adversarial collision resistance. Short anchors keep the tokens the
# model echoes back cheap. Do not "harden" this without revisiting that model.
HASH_SIZE = 4

_BOM = "﻿"


@dataclass(frozen=True)
class HashEdit:
    line: int
    hash: str
    content: str


def hashread(path: str) -> str:
    """Read a text file as LINE:HASH|content rows."""
    normalized = _read_normalized(Path(path))
    return "\n".join(
        _format_hashline(idx, content) for idx, content in enumerate(normalized.lines, start=1)
    )


def hashedit(path: str, edits: list[dict[str, Any] | HashEdit]) -> str:
    """Apply single-line edits only when the current line hash matches the anchor."""
    target = Path(path)
    normalized = _read_normalized(target)
    lines = list(normalized.lines)
    parsed = [_coerce_edit(edit) for edit in edits]
    seen: set[int] = set()

    for edit in parsed:
        if edit.line < 1 or edit.line > len(lines):
            raise ValueError(f"line out of range: {edit.line}")
        if edit.line in seen:
            raise ValueError(f"duplicate edit for line: {edit.line}")
        if "\n" in edit.content or "\r" in edit.content:
            raise ValueError("hashedit edits exactly one line; content must not contain newlines")
        seen.add(edit.line)

        current = lines[edit.line - 1]
        current_hash = line_hash(current)
        if current_hash != edit.hash:
            raise ValueError(
                f"stale edit for line {edit.line}: expected {edit.hash}, current {current_hash}"
            )

    changes = []
    for edit in parsed:
        before = _format_hashline(edit.line, lines[edit.line - 1])
        lines[edit.line - 1] = edit.content
        after = _format_hashline(edit.line, edit.content)
        changes.append({"line": edit.line, "before": before, "after": after})

    target.write_text(_serialize(normalized, lines), newline="")
    return "\n".join(f"- {change['before']}\n+ {change['after']}" for change in changes)


def line_hash(content: str) -> str:
    return blake2b(content.encode("utf-8"), digest_size=HASH_SIZE).hexdigest()


@dataclass(frozen=True)
class _NormalizedFile:
    """LF-normalized, BOM-stripped view plus the original shape needed to restore it."""

    lines: list[str]
    ending: str
    bom: str
    final_newline: bool


def _read_normalized(path: Path) -> _NormalizedFile:
    # newline="" disables universal-newline translation so we observe the real
    # bytes; hashing/editing happen on LF-normalized content, original shape
    # (BOM + ending) is restored on write. Mirrors oh-my-pi's hashline seam.
    raw = path.read_text(newline="")
    bom = _BOM if raw.startswith(_BOM) else ""
    if bom:
        raw = raw[len(_BOM) :]
    ending = "\r\n" if "\r\n" in raw else "\n"
    lf = raw.replace("\r\n", "\n").replace("\r", "\n")
    if lf == "":
        return _NormalizedFile(lines=[], ending=ending, bom=bom, final_newline=False)
    final_newline = lf.endswith("\n")
    lines = lf.split("\n")
    if final_newline:
        lines.pop()  # drop the trailing "" the split appends after the last newline
    return _NormalizedFile(lines=lines, ending=ending, bom=bom, final_newline=final_newline)


def _serialize(normalized: _NormalizedFile, lines: list[str]) -> str:
    body = "\n".join(lines)
    if normalized.final_newline:
        body += "\n"
    return normalized.bom + body.replace("\n", normalized.ending)


def _coerce_edit(edit: dict[str, Any] | HashEdit) -> HashEdit:
    if isinstance(edit, HashEdit):
        return edit
    anchor = edit.get("hash", edit.get("line_hash"))
    if anchor is None:
        raise ValueError("edit must include hash")
    return HashEdit(line=int(edit["line"]), hash=str(anchor), content=str(edit["content"]))


def _format_hashline(line: int, content: str) -> str:
    return f"{line}:{line_hash(content)}|{content}"
