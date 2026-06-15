from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from agentkit.stores.memory.base import MemoryProvider
from agentkit.stores.memory.index import IndexedFact, MemoryIndex
from agentkit.stores.memory.types import MemoryBlock, MemoryContext, MemoryWrite, RetrievedMemory


class FilesMemoryProvider(MemoryProvider):
    def __init__(self, root: str | Path, *, top_k: int = 10) -> None:
        self.root = Path(root).expanduser()
        self.core_dir = self.root / "core"
        self.facts_dir = self.root / "facts"
        self.index_dir = self.root / "index"
        self.top_k = top_k
        for path in [self.core_dir, self.facts_dir, self.index_dir]:
            path.mkdir(parents=True, exist_ok=True)
        self.index = MemoryIndex(self.index_dir / "facts.db")
        self._rebuild_index()

    def prefetch(self, ctx: MemoryContext) -> RetrievedMemory:
        query = ctx.seed_query()
        retrieved = self.search(query, self.top_k, project_id=ctx.project_id)
        blocks = [*self._core_blocks(), *retrieved.blocks]
        return RetrievedMemory(
            blocks=blocks,
            query=query,
            token_estimate=sum(len(block.text.split()) for block in blocks),
        )

    def sync_turn(self, writes: list[MemoryWrite], ctx: MemoryContext) -> None:
        for write in writes:
            if write.op == "append":
                self._append_fact(write, project_id=ctx.project_id)
            elif write.op == "replace":
                self._replace_fact(write, project_id=ctx.project_id)
            else:
                raise ValueError(f"unsupported memory write op: {write.op}")
        if writes:
            self._rebuild_index()

    def shutdown(self) -> None:
        self.index.close()

    def search(self, query: str, k: int = 10, *, project_id: str | None = None) -> RetrievedMemory:
        pid = project_id or ""
        fact_ids = self.index.search(query, project_id=pid, limit=k)
        fact_by_id = {fact.id: fact for fact in self._facts()}
        blocks = [
            MemoryBlock(
                id=fact.id,
                kind="fact",
                text=fact.body,
                source=str(fact.path),
                summary=fact.summary,
                entities=fact.entities,
                project_id=fact.project_id,
            )
            for fact in (fact_by_id[fact_id] for fact_id in fact_ids if fact_id in fact_by_id)
        ]
        return RetrievedMemory(
            blocks=blocks,
            query=query,
            token_estimate=sum(len(block.text.split()) for block in blocks),
        )

    def _core_blocks(self) -> list[MemoryBlock]:
        blocks = []
        for kind, filename in [("persona", "persona.md"), ("user", "user.md")]:
            path = self.core_dir / filename
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            blocks.append(MemoryBlock(id=kind, kind=kind, text=text, source=str(path)))
        return blocks

    def _append_fact(self, write: MemoryWrite, *, project_id: str) -> Path:
        fact_id = write.target or f"fact-{uuid.uuid4().hex[:12]}"
        path = self.facts_dir / f"{_slug(fact_id)}.md"
        if path.exists():
            raise FileExistsError(f"memory fact already exists: {fact_id}")
        return self._write_fact(path, write, fact_id=fact_id, project_id=project_id)

    def _replace_fact(self, write: MemoryWrite, *, project_id: str) -> Path:
        if not write.target:
            raise ValueError("replace requires target")
        existing = self._fact_path(write.target)
        if existing is None:
            raise FileNotFoundError(f"memory fact not found: {write.target}")
        return self._write_fact(existing, write, fact_id=write.target, project_id=project_id)

    def _write_fact(
        self,
        path: Path,
        write: MemoryWrite,
        *,
        fact_id: str,
        project_id: str,
    ) -> Path:
        metadata = {
            "id": fact_id,
            "summary": write.summary or write.text.splitlines()[0][:120],
            "entities": write.entities,
            "project_id": project_id,
            "source_session": write.source_session,
        }
        content = f"---\n{yaml.safe_dump(metadata, sort_keys=True)}---\n{write.text}\n"
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
        return path

    def _fact_path(self, fact_id: str) -> Path | None:
        for fact in self._facts():
            if fact.id == fact_id:
                return fact.path
        return None

    def _rebuild_index(self) -> None:
        self.index.rebuild(self._facts())

    def _facts(self) -> list[IndexedFact]:
        facts: list[IndexedFact] = []
        for path in sorted(self.facts_dir.glob("*.md")):
            try:
                metadata, body = _read_fact(path)
            except Exception as exc:
                logger.warning("skipping corrupt memory fact {}: {}", path, exc)
                continue
            fact_id = str(metadata.get("id") or path.stem)
            project_id = str(metadata.get("project_id") or "")
            summary = str(metadata.get("summary") or "")
            entities = [str(entity).lower() for entity in metadata.get("entities") or []]
            facts.append(
                IndexedFact(
                    id=fact_id,
                    path=path,
                    project_id=project_id,
                    summary=summary,
                    body=body,
                    entities=entities,
                )
            )
        return facts


def _read_fact(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    _start, rest = text.split("---\n", 1)
    raw_meta, body = rest.split("---\n", 1)
    metadata = yaml.safe_load(raw_meta) or {}
    if not isinstance(metadata, dict):
        raise ValueError("frontmatter must be a mapping")
    return metadata, body.strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-").lower()
    return slug or f"fact-{uuid.uuid4().hex[:12]}"
