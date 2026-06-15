from __future__ import annotations

from dataclasses import dataclass
from hashlib import blake2b
from pathlib import Path
from typing import Any

HASH_SIZE = 4


@dataclass(frozen=True)
class HashEdit:
    line: int
    hash: str
    content: str


def hashread(path: str) -> str:
    """Read a text file as LINE:HASH|content rows."""
    rows = []
    for idx, record in enumerate(_read_line_records(Path(path)), start=1):
        rows.append(_format_hashline(idx, record.content))
    return "\n".join(rows)


def hashedit(path: str, edits: list[dict[str, Any] | HashEdit]) -> str:
    """Apply single-line edits only when the current line hash matches the anchor."""
    target = Path(path)
    records = _read_line_records(target)
    parsed = [_coerce_edit(edit) for edit in edits]
    seen: set[int] = set()

    for edit in parsed:
        if edit.line < 1 or edit.line > len(records):
            raise ValueError(f"line out of range: {edit.line}")
        if edit.line in seen:
            raise ValueError(f"duplicate edit for line: {edit.line}")
        if "\n" in edit.content or "\r" in edit.content:
            raise ValueError("hashedit edits exactly one line; content must not contain newlines")
        seen.add(edit.line)

        current = records[edit.line - 1].content
        current_hash = line_hash(current)
        if current_hash != edit.hash:
            raise ValueError(
                f"stale edit for line {edit.line}: expected {edit.hash}, current {current_hash}"
            )

    changes = []
    for edit in parsed:
        record = records[edit.line - 1]
        before = _format_hashline(edit.line, record.content)
        records[edit.line - 1] = _LineRecord(content=edit.content, ending=record.ending)
        after = _format_hashline(edit.line, edit.content)
        changes.append({"line": edit.line, "before": before, "after": after})

    target.write_text("".join(record.content + record.ending for record in records))
    return "\n".join(f"- {change['before']}\n+ {change['after']}" for change in changes)


def line_hash(content: str) -> str:
    return blake2b(content.encode("utf-8"), digest_size=HASH_SIZE).hexdigest()


@dataclass(frozen=True)
class _LineRecord:
    content: str
    ending: str


def _read_line_records(path: Path) -> list[_LineRecord]:
    text = path.read_text()
    records: list[_LineRecord] = []
    for raw in text.splitlines(keepends=True):
        if raw.endswith("\r\n"):
            records.append(_LineRecord(content=raw[:-2], ending="\r\n"))
        elif raw.endswith("\n") or raw.endswith("\r"):
            records.append(_LineRecord(content=raw[:-1], ending=raw[-1]))
        else:
            records.append(_LineRecord(content=raw, ending=""))
    return records


def _coerce_edit(edit: dict[str, Any] | HashEdit) -> HashEdit:
    if isinstance(edit, HashEdit):
        return edit
    anchor = edit.get("hash", edit.get("line_hash"))
    if anchor is None:
        raise ValueError("edit must include hash")
    return HashEdit(line=int(edit["line"]), hash=str(anchor), content=str(edit["content"]))


def _format_hashline(line: int, content: str) -> str:
    return f"{line}:{line_hash(content)}|{content}"
