from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from agentkit.kernel.providers.types import Message, ToolCall
from agentkit.stores._sqlite import SQLiteStore
from agentkit.stores.project import resolve_project_id
from agentkit.stores.session.base import SessionStore
from agentkit.stores.session.types import EndReason, SearchHit, SessionMeta, SessionSource

SCHEMA_VERSION = 1


class SQLiteSessionStore(SessionStore):
    def __init__(
        self,
        path: str | Path,
        *,
        project_id: str | None = None,
        cwd: str | Path = ".",
    ) -> None:
        self._db = SQLiteStore(path)
        self.project_id = project_id or resolve_project_id(cwd)
        self._init_schema()

    def close(self) -> None:
        self._db.close()

    def create_session(
        self,
        *,
        source: SessionSource,
        model: str,
        model_config: dict[str, Any],
        system_prompt: str,
        parent_session_id: str | None = None,
        title: str | None = None,
    ) -> str:
        session_id = str(uuid.uuid4())
        now = time.time()

        def write(conn):
            conn.execute(
                """
                INSERT INTO sessions (
                    id, project_id, source, model, model_config, system_prompt, title,
                    parent_session_id, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    self.project_id,
                    source,
                    model,
                    json.dumps(model_config, sort_keys=True),
                    system_prompt,
                    title,
                    parent_session_id,
                    now,
                ),
            )
            return session_id

        return self._db.write(write)

    def add_message(self, session_id: str, msg: Message) -> int:
        seq = self._next_seq(session_id)
        tool_calls = [call.model_dump(mode="json") for call in msg.tool_calls]
        content = _message_content_text(msg)
        timestamp = time.time()

        def write(conn):
            cursor = conn.execute(
                """
                INSERT INTO messages (
                    session_id, seq, role, content, tool_calls, tool_call_id, tool_name, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    seq,
                    msg.role,
                    content,
                    json.dumps(tool_calls, sort_keys=True),
                    msg.tool_call_id,
                    msg.name,
                    timestamp,
                ),
            )
            conn.execute(
                """
                UPDATE sessions
                   SET message_count = message_count + 1,
                       tool_call_count = tool_call_count + ?
                 WHERE id = ?
                """,
                (len(tool_calls), session_id),
            )
            return int(cursor.lastrowid)

        return self._db.write(write)

    def get_messages(self, session_id: str, *, include_chain: bool = True) -> list[Message]:
        session_ids = self._chain_ids(session_id) if include_chain else [session_id]
        messages: list[Message] = []
        for sid in session_ids:
            rows = self._db.execute(
                """
                SELECT role, content, tool_calls, tool_call_id, tool_name
                  FROM messages
                 WHERE session_id = ?
                 ORDER BY seq ASC
                """,
                (sid,),
            ).fetchall()
            messages.extend(_row_to_message(row) for row in rows)
        return messages

    def latest_session(self, *, source: SessionSource | None = None) -> str | None:
        if source is None:
            row = self._db.execute(
                """
                SELECT id FROM sessions
                 WHERE project_id = ?
                 ORDER BY started_at DESC
                 LIMIT 1
                """,
                (self.project_id,),
            ).fetchone()
        else:
            row = self._db.execute(
                """
                SELECT id FROM sessions
                 WHERE project_id = ? AND source = ?
                 ORDER BY started_at DESC
                 LIMIT 1
                """,
                (self.project_id, source),
            ).fetchone()
        return str(row["id"]) if row else None

    def list_sessions(self, *, limit: int = 20) -> list[SessionMeta]:
        rows = self._db.execute(
            """
            SELECT * FROM sessions
             WHERE project_id = ?
             ORDER BY started_at DESC
             LIMIT ?
            """,
            (self.project_id, limit),
        ).fetchall()
        return [_row_to_meta(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        context_radius: int = 5,
        cross_project: bool = False,
    ) -> list[SearchHit]:
        project_clause = "" if cross_project else "AND s.project_id = ?"
        params: list[Any] = [query]
        if not cross_project:
            params.append(self.project_id)
        params.append(limit)
        rows = self._db.execute(
            f"""
            SELECT m.id AS message_id,
                   m.session_id,
                   m.role,
                   m.timestamp,
                   s.title AS session_title,
                   s.project_id,
                   snippet(messages_fts, 0, '>>>', '<<<', '...', 12) AS snippet
              FROM messages_fts
              JOIN messages m ON m.id = messages_fts.rowid
              JOIN sessions s ON s.id = m.session_id
             WHERE messages_fts MATCH ?
                   {project_clause}
             ORDER BY rank
             LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [
            SearchHit(
                session_id=row["session_id"],
                message_id=row["message_id"],
                role=row["role"],
                timestamp=row["timestamp"],
                snippet=row["snippet"],
                context=self._context(row["session_id"], row["message_id"], context_radius),
                session_title=row["session_title"],
                project_id=row["project_id"],
            )
            for row in rows
        ]

    def end_session(self, session_id: str, *, reason: EndReason) -> None:
        now = time.time()

        def write(conn):
            conn.execute(
                "UPDATE sessions SET ended_at = ?, end_reason = ? WHERE id = ?",
                (now, reason, session_id),
            )

        self._db.write(write)

    def _init_schema(self) -> None:
        def write(conn):
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    model TEXT,
                    model_config TEXT,
                    system_prompt TEXT,
                    title TEXT,
                    parent_session_id TEXT,
                    started_at REAL NOT NULL,
                    ended_at REAL,
                    end_reason TEXT,
                    message_count INTEGER DEFAULT 0,
                    tool_call_count INTEGER DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_project_started
                    ON sessions(project_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_sessions_parent
                    ON sessions(parent_session_id);

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id),
                    seq INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    tool_calls TEXT,
                    tool_call_id TEXT,
                    tool_name TEXT,
                    reasoning TEXT,
                    reasoning_content TEXT,
                    reasoning_details TEXT,
                    finish_reason TEXT,
                    token_count INTEGER,
                    timestamp REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_session_seq
                    ON messages(session_id, seq);

                CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                    content,
                    content='messages',
                    content_rowid='id',
                    tokenize='porter unicode61'
                );
                CREATE TRIGGER IF NOT EXISTS messages_fts_insert
                    AFTER INSERT ON messages BEGIN
                    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                END;
                CREATE TRIGGER IF NOT EXISTS messages_fts_delete
                    AFTER DELETE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.id, old.content);
                END;
                CREATE TRIGGER IF NOT EXISTS messages_fts_update
                    AFTER UPDATE ON messages BEGIN
                    INSERT INTO messages_fts(messages_fts, rowid, content)
                    VALUES('delete', old.id, old.content);
                    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
                END;

                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
                """
            )
            current = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if current is None:
                conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif int(current["version"]) != SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported sessions.db schema version {current['version']}; "
                    f"expected {SCHEMA_VERSION}"
                )

        self._db.write(write)

    def _next_seq(self, session_id: str) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS seq FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return int(row["seq"])

    def _chain_ids(self, session_id: str) -> list[str]:
        out: list[str] = []
        current = session_id
        while current:
            row = self._db.execute(
                "SELECT parent_session_id FROM sessions WHERE id = ?",
                (current,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown session_id: {current}")
            out.append(current)
            current = row["parent_session_id"]
        return list(reversed(out))

    def _context(self, session_id: str, message_id: int, radius: int) -> list[Message]:
        hit = self._db.execute(
            "SELECT seq FROM messages WHERE id = ? AND session_id = ?",
            (message_id, session_id),
        ).fetchone()
        if hit is None:
            return []
        seq = int(hit["seq"])
        rows = self._db.execute(
            """
            SELECT role, content, tool_calls, tool_call_id, tool_name
              FROM messages
             WHERE session_id = ? AND seq BETWEEN ? AND ?
             ORDER BY seq ASC
            """,
            (session_id, seq - radius, seq + radius),
        ).fetchall()
        return [_row_to_message(row) for row in rows]


def _message_content_text(message: Message) -> str | None:
    if message.content is None:
        return None
    if isinstance(message.content, str):
        return message.content
    return "\n".join(block.text for block in message.content)


def _row_to_message(row) -> Message:
    raw_calls = json.loads(row["tool_calls"] or "[]")
    return Message(
        role=row["role"],
        content=row["content"],
        tool_calls=[ToolCall.model_validate(call) for call in raw_calls],
        tool_call_id=row["tool_call_id"],
        name=row["tool_name"],
    )


def _row_to_meta(row) -> SessionMeta:
    return SessionMeta(
        id=row["id"],
        project_id=row["project_id"],
        source=row["source"],
        model=row["model"] or "",
        title=row["title"],
        parent_session_id=row["parent_session_id"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        end_reason=row["end_reason"],
        message_count=row["message_count"],
        tool_call_count=row["tool_call_count"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        cache_read_tokens=row["cache_read_tokens"],
        cache_write_tokens=row["cache_write_tokens"],
        reasoning_tokens=row["reasoning_tokens"],
    )
