from __future__ import annotations

from hashlib import blake2b
from pathlib import Path

HASH_SIZE = 4


def hashread(path: str) -> str:
    """Read a text file as LINE:HASH|content rows."""
    rows = []
    for idx, record in enumerate(_read_line_records(Path(path)), start=1):
        rows.append(_format_hashline(idx, record.content))
    return "\n".join(rows)


def line_hash(content: str) -> str:
    return blake2b(content.encode("utf-8"), digest_size=HASH_SIZE).hexdigest()


class _LineRecord:
    def __init__(self, *, content: str, ending: str) -> None:
        self.content = content
        self.ending = ending


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


def _format_hashline(line: int, content: str) -> str:
    return f"{line}:{line_hash(content)}|{content}"
