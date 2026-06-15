from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from agentkit.stores._sqlite import SQLiteStore
from agentkit.stores.project import resolve_project_id
from agentkit.stores.trace.types import Annotation, SkillRef, StepRecord, TraceRecord

SCHEMA_VERSION = 1


class SQLiteTraceStore:
    def __init__(
        self,
        path: str | Path,
        *,
        traces_dir: str | Path | None = None,
        project_id: str | None = None,
        cwd: str | Path = ".",
    ) -> None:
        self._db = SQLiteStore(path)
        self.project_id = project_id or resolve_project_id(cwd)
        self.traces_dir = Path(traces_dir or Path(path).with_name("traces")).expanduser()
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def close(self) -> None:
        self._db.close()

    def start_trace(
        self,
        *,
        session_id: str,
        task: str,
        agent_role: str = "main",
        parent_trace_id: str | None = None,
        active_skills: list[SkillRef] | None = None,
    ) -> str:
        trace_id = str(uuid.uuid4())
        body_path = self.traces_dir / f"{trace_id}.jsonl"
        started_at = time.time()
        body_path.touch(exist_ok=False)

        def write(conn):
            conn.execute(
                """
                INSERT INTO traces (
                    trace_id, project_id, session_id, parent_trace_id, agent_role, task,
                    outcome, outcome_source, body_path, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'unknown', 'none', ?, ?)
                """,
                (
                    trace_id,
                    self.project_id,
                    session_id,
                    parent_trace_id,
                    agent_role,
                    task,
                    str(body_path),
                    started_at,
                ),
            )
            for skill in active_skills or []:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO trace_skills(trace_id, skill_name, version, was_used)
                    VALUES (?, ?, ?, 0)
                    """,
                    (trace_id, skill.name, skill.version),
                )
            return trace_id

        return self._db.write(write)

    def append_step(self, step: StepRecord) -> None:
        body_path = self._body_path(step.trace_id)
        payload = step.model_dump(mode="json")

        def write(_conn):
            with body_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")

        self._db.write(write)

    def record_turn(
        self,
        *,
        trace_id: str,
        turn_id: str,
        assistant_msg_id: int | None = None,
        turn_outcome: str | None = None,
    ) -> None:
        seq = self._next_turn_seq(trace_id)

        def write(conn):
            conn.execute(
                """
                INSERT OR REPLACE INTO turns (
                    turn_id, trace_id, seq, assistant_msg_id, turn_outcome
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (turn_id, trace_id, seq, assistant_msg_id, turn_outcome),
            )

        self._db.write(write)

    def add_annotation(self, trace_id: str, annotation: Annotation) -> int:
        created_at = time.time()

        def write(conn):
            cursor = conn.execute(
                """
                INSERT INTO annotations (
                    trace_id, kind, source, confidence, target, target_id,
                    evidence, detector, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    annotation.kind,
                    annotation.source,
                    annotation.confidence,
                    annotation.target,
                    annotation.target_id,
                    annotation.evidence,
                    annotation.detector,
                    created_at,
                ),
            )
            return int(cursor.lastrowid)

        return self._db.write(write)

    def mark_skill_used(self, trace_id: str, skill: SkillRef) -> None:
        def write(conn):
            conn.execute(
                """
                INSERT INTO trace_skills(trace_id, skill_name, version, was_used)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(trace_id, skill_name) DO UPDATE SET
                    version = excluded.version,
                    was_used = 1
                """,
                (trace_id, skill.name, skill.version),
            )

        self._db.write(write)

    def seal_trace(
        self,
        trace_id: str,
        *,
        outcome: str = "unknown",
        outcome_source: str = "none",
        score: float | None = None,
        feedback: str | None = None,
        budget_used: int = 0,
    ) -> None:
        ended_at = time.time()

        def write(conn):
            row = conn.execute(
                "SELECT sealed FROM traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown trace_id: {trace_id}")
            if row["sealed"]:
                return
            conn.execute(
                """
                UPDATE traces
                   SET outcome = ?,
                       outcome_source = ?,
                       score = ?,
                       feedback = ?,
                       ended_at = ?,
                       budget_used = ?,
                       sealed = 1
                 WHERE trace_id = ?
                """,
                (outcome, outcome_source, score, feedback, ended_at, budget_used, trace_id),
            )
            seal_record = {
                "kind": "seal",
                "trace_id": trace_id,
                "outcome": outcome,
                "outcome_source": outcome_source,
                "score": score,
                "ended_at": ended_at,
            }
            with self._body_path(trace_id).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(seal_record, sort_keys=True) + "\n")

        self._db.write(write)

    def get_trace(self, trace_id: str) -> TraceRecord:
        row = self._db.execute(
            "SELECT * FROM traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown trace_id: {trace_id}")
        return self._row_to_trace(row)

    def replay_set(self, skill_name: str, *, min_outcome_quality: float = 0.0) -> list[TraceRecord]:
        rows = self._db.execute(
            """
            SELECT t.*
              FROM traces t
              JOIN trace_skills ts ON ts.trace_id = t.trace_id
             WHERE ts.skill_name = ?
               AND t.project_id = ?
               AND t.outcome != 'unknown'
               AND COALESCE(t.score, CASE WHEN t.outcome = 'success' THEN 1.0 ELSE 0.0 END) >= ?
             ORDER BY t.started_at DESC
            """,
            (skill_name, self.project_id, min_outcome_quality),
        ).fetchall()
        return [self._row_to_trace(row) for row in rows]

    def failure_set(self, skill_name: str) -> list[TraceRecord]:
        rows = self._db.execute(
            """
            SELECT t.*
              FROM traces t
              JOIN trace_skills ts ON ts.trace_id = t.trace_id
             WHERE ts.skill_name = ?
               AND t.project_id = ?
               AND t.outcome = 'failure'
             ORDER BY t.started_at DESC
            """,
            (skill_name, self.project_id),
        ).fetchall()
        return [self._row_to_trace(row) for row in rows]

    def success_set(self, skill_name: str) -> list[TraceRecord]:
        rows = self._db.execute(
            """
            SELECT t.*
              FROM traces t
              JOIN trace_skills ts ON ts.trace_id = t.trace_id
             WHERE ts.skill_name = ?
               AND t.project_id = ?
               AND t.outcome = 'success'
             ORDER BY t.started_at DESC
            """,
            (skill_name, self.project_id),
        ).fetchall()
        return [self._row_to_trace(row) for row in rows]

    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM trace_meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        def write(conn):
            conn.execute(
                """
                INSERT INTO trace_meta(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )

        self._db.write(write)

    def _init_schema(self) -> None:
        def write(conn):
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS traces (
                    trace_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    parent_trace_id TEXT,
                    agent_role TEXT NOT NULL,
                    task TEXT,
                    outcome TEXT,
                    outcome_source TEXT,
                    score REAL,
                    feedback TEXT,
                    body_path TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    budget_used INTEGER DEFAULT 0,
                    handoff_payload TEXT,
                    sealed INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_traces_project_outcome
                    ON traces(project_id, outcome, outcome_source);

                CREATE TABLE IF NOT EXISTS turns (
                    turn_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL REFERENCES traces(trace_id),
                    seq INTEGER NOT NULL,
                    assistant_msg_id INTEGER,
                    turn_outcome TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_turns_trace_seq ON turns(trace_id, seq);

                CREATE TABLE IF NOT EXISTS trace_skills (
                    trace_id TEXT NOT NULL REFERENCES traces(trace_id),
                    skill_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    was_used INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (trace_id, skill_name)
                );
                CREATE INDEX IF NOT EXISTS idx_trace_skills_name
                    ON trace_skills(skill_name, version);

                CREATE TABLE IF NOT EXISTS annotations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL REFERENCES traces(trace_id),
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    target TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    evidence TEXT,
                    detector TEXT,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_annotations_trace
                    ON annotations(trace_id, kind);

                CREATE TABLE IF NOT EXISTS trace_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
                """
            )
            current = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if current is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif int(current["version"]) != SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported trace.db schema version {current['version']}; "
                    f"expected {SCHEMA_VERSION}"
                )

        self._db.write(write)

    def _row_to_trace(self, row) -> TraceRecord:
        trace_id = row["trace_id"]
        return TraceRecord(
            trace_id=trace_id,
            project_id=row["project_id"],
            session_id=row["session_id"],
            parent_trace_id=row["parent_trace_id"],
            agent_role=row["agent_role"],
            task=row["task"] or "",
            outcome=row["outcome"],
            outcome_source=row["outcome_source"],
            score=row["score"],
            feedback=row["feedback"],
            body_path=row["body_path"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            budget_used=row["budget_used"],
            handoff_payload=json.loads(row["handoff_payload"] or "null"),
            sealed=bool(row["sealed"]),
            active_skills=self._skills(trace_id, was_used=False),
            used_skills=self._skills(trace_id, was_used=True),
            steps=self._steps(trace_id),
            annotations=self._annotations(trace_id),
        )

    def _skills(self, trace_id: str, *, was_used: bool) -> list[SkillRef]:
        if was_used:
            rows = self._db.execute(
                """
                SELECT skill_name, version FROM trace_skills
                 WHERE trace_id = ? AND was_used = 1
                 ORDER BY skill_name
                """,
                (trace_id,),
            ).fetchall()
        else:
            rows = self._db.execute(
                """
                SELECT skill_name, version FROM trace_skills
                 WHERE trace_id = ?
                 ORDER BY skill_name
                """,
                (trace_id,),
            ).fetchall()
        return [SkillRef(name=row["skill_name"], version=row["version"]) for row in rows]

    def _annotations(self, trace_id: str) -> list[Annotation]:
        rows = self._db.execute(
            """
            SELECT kind, source, confidence, target, target_id, evidence, detector
              FROM annotations
             WHERE trace_id = ?
             ORDER BY id ASC
            """,
            (trace_id,),
        ).fetchall()
        return [Annotation(**dict(row)) for row in rows]

    def _steps(self, trace_id: str) -> list[StepRecord]:
        steps: list[StepRecord] = []
        for item in self._jsonl_records(trace_id):
            if item.get("kind") == "seal":
                continue
            steps.append(StepRecord.model_validate(item))
        return steps

    def _jsonl_records(self, trace_id: str) -> list[dict[str, Any]]:
        body_path = self._body_path(trace_id)
        if not body_path.exists():
            return []
        return [
            json.loads(line)
            for line in body_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _body_path(self, trace_id: str) -> Path:
        row = self._db.execute(
            "SELECT body_path FROM traces WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown trace_id: {trace_id}")
        return Path(row["body_path"])

    def _next_turn_seq(self, trace_id: str) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM turns WHERE trace_id = ?",
            (trace_id,),
        ).fetchone()
        return int(row["seq"])
