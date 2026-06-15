from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from agentkit.stores._sqlite import SQLiteStore


@dataclass(frozen=True)
class IndexedFact:
    id: str
    path: Path
    project_id: str
    summary: str
    body: str
    entities: list[str]


class MemoryIndex:
    def __init__(self, path: str | Path) -> None:
        self._db = SQLiteStore(path)
        self._init_schema()

    def close(self) -> None:
        self._db.close()

    def rebuild(self, facts: list[IndexedFact]) -> None:
        def write(conn):
            conn.execute("DELETE FROM fact_entities")
            conn.execute("DELETE FROM facts")
            for fact in facts:
                conn.execute(
                    """
                    INSERT INTO facts(id, path, project_id, summary, body)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (fact.id, str(fact.path), fact.project_id, fact.summary, fact.body),
                )
                conn.executemany(
                    "INSERT INTO fact_entities(fact_id, entity) VALUES (?, ?)",
                    [(fact.id, entity.lower()) for entity in fact.entities],
                )

        self._db.write(write)

    def search(self, query: str, *, project_id: str, limit: int = 10) -> list[str]:
        bm25 = self._bm25(query, project_id)
        semantic = self._summary_overlap(query, project_id)
        entity = self._entity_overlap(query, project_id)
        scores: dict[str, float] = {}
        for ranked in [bm25, semantic, entity]:
            for rank, fact_id in enumerate(ranked, start=1):
                scores[fact_id] = scores.get(fact_id, 0.0) + 1 / (60 + rank)
        return [
            fact_id
            for fact_id, _score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[
                :limit
            ]
        ]

    def _init_schema(self) -> None:
        def write(conn):
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    summary TEXT,
                    body TEXT
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                    summary,
                    body,
                    content='facts',
                    content_rowid='rowid',
                    tokenize='porter unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS facts_fts_insert
                    AFTER INSERT ON facts BEGIN
                    INSERT INTO facts_fts(rowid, summary, body)
                    VALUES (new.rowid, new.summary, new.body);
                END;
                CREATE TRIGGER IF NOT EXISTS facts_fts_delete
                    AFTER DELETE ON facts BEGIN
                    INSERT INTO facts_fts(facts_fts, rowid, summary, body)
                    VALUES('delete', old.rowid, old.summary, old.body);
                END;
                CREATE TABLE IF NOT EXISTS fact_entities (
                    fact_id TEXT NOT NULL REFERENCES facts(id),
                    entity TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_fact_entities_entity
                    ON fact_entities(entity);
                """
            )

        self._db.write(write)

    def _bm25(self, query: str, project_id: str) -> list[str]:
        try:
            rows = self._db.execute(
                """
                SELECT f.id
                  FROM facts_fts
                  JOIN facts f ON f.rowid = facts_fts.rowid
                 WHERE facts_fts MATCH ? AND f.project_id = ?
                 ORDER BY rank
                """,
                (query, project_id),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [row["id"] for row in rows]

    def _summary_overlap(self, query: str, project_id: str) -> list[str]:
        terms = _terms(query)
        rows = self._db.execute(
            "SELECT id, summary FROM facts WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        scored = []
        for row in rows:
            overlap = len(terms & _terms(row["summary"] or ""))
            if overlap:
                scored.append((row["id"], overlap))
        return [fact_id for fact_id, _ in sorted(scored, key=lambda item: (-item[1], item[0]))]

    def _entity_overlap(self, query: str, project_id: str) -> list[str]:
        terms = _terms(query)
        if not terms:
            return []
        placeholders = ",".join("?" for _ in terms)
        rows = self._db.execute(
            f"""
            SELECT f.id, COUNT(*) AS hits
              FROM fact_entities e
              JOIN facts f ON f.id = e.fact_id
             WHERE f.project_id = ? AND e.entity IN ({placeholders})
             GROUP BY f.id
             ORDER BY hits DESC, f.id ASC
            """,
            (project_id, *terms),
        ).fetchall()
        return [row["id"] for row in rows]


def _terms(text: str) -> set[str]:
    return {term.strip(".,:;!?()[]{}").lower() for term in text.split() if term.strip()}

