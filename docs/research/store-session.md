# Ring-2 Store — Session (conversation record persistence)

Date: 2026-06-15
Module: `agentkit/stores/session/` (Ring-2, interface + default impl)
Spec refs: §4 (Stores table), §4.2 (trace boundary), Decisions #4b, #7, #15, #17, #29; e2e #3, #17.

---

## Module scope

The session store is the **SSoT for the conversation record** — the full, replayable
message history of every conversation, keyed by session. It is the storage layer that
powers `--continue` / `--resume` and the `session_search` tool (cross-session recall).

In scope:
- Persist every *complete* message (user / assistant / tool-result), tool calls, and
  reasoning (separated from visible content).
- Persist session metadata (model, timestamps, token/cost counters, parent chain).
- FTS5 full-text index over message content → `session_search` tool.
- Multi-process safety: `agentkit-server` daemon (cron/dream/SSE) and `agentkit-cli`
  may both touch the same `sessions.db` concurrently.

Explicitly OUT of scope (boundaries enforced elsewhere):
- **`stream_delta` is NOT persisted** (Decision #15) — per-token render projection only;
  only the *complete* assistant message lands here. SSoT for messages stays the session
  store, not the SSE stream.
- **Trace material is NOT here** (Decision #17, §4.2) — see "Boundary with trace store".
- **Memory facts are NOT here** (Decision #17a) — memory is a separate Ring-2 store.

The default impl mirrors Hermes Agent's `state.db` design (the spec's stated reference):
SQLite WAL + FTS5, external-content FTS table kept in sync by triggers, short
busy-timeout + application-level BEGIN IMMEDIATE retry with jitter. Hermes's parameters
(20–150ms jitter, 15 retries, 1s timeout, checkpoint every 50 writes) are adopted
verbatim because they are battle-tested against exactly our topology (a multi-platform
gateway daemon + CLI sharing one DB file).

---

## Recommended design

1. **Single DB file `sessions.db`**, opened by every process that needs it. No central
   server-owns-the-DB rule — WAL is explicitly designed for many readers + one writer
   across processes, and SQLite's file locking coordinates the cross-process case. The
   daemon does not get exclusive ownership; CLI opens the same file directly. This keeps
   the in-process SDK path (Decision #3) working without requiring the server to be up.

2. **Message shape = provider-agnostic Alfred message type**, not a vendor format. The
   row stores the canonical fields needed to *reconstruct* a provider request on resume:
   `role`, `content` (visible text), `tool_calls` (JSON, assistant→tool requests),
   `tool_call_id` + `tool_name` (tool-result correlation), and **reasoning kept in its
   own columns** (`reasoning`, `reasoning_content`, `reasoning_details`) distinct from
   visible `content`. Reasoning-separate-from-visible matters because (a) FTS should index
   visible content + reasoning is optional-to-index, (b) some providers require replaying
   reasoning blocks (Anthropic extended thinking / OpenAI reasoning items) and some forbid
   it — storing them separately lets the provider boundary layer decide per-vendor.

3. **External-content FTS5** (`content=messages`, `content_rowid=id`) so message text is
   stored once; FTS holds only the index. Three triggers (insert/delete/update) keep it
   in sync. This is the Hermes pattern and the documented FTS5 best practice (avoid
   double-storing the text).

4. **Writes go through one `_write()` helper**: `BEGIN IMMEDIATE` + try/commit/rollback,
   wrapped in a jittered retry loop. All INSERT/UPDATE/DELETE funnel through it. Reads use
   plain `SELECT` (WAL lets them proceed without blocking the writer).

5. **`--continue` vs `--resume` differ only in *which* session id is selected**, then both
   call the same `get_messages(session_id)` to rehydrate. A **recap** (LLM summary of the
   prior session) is injected at `session_start` *only when the rehydrated history would
   blow the budget or when resuming a compressed chain* — otherwise the full history is
   replayed verbatim (cache-friendly, frozen-prefix per Decision #21/#29).

6. **`session_search` tool** runs FTS5 `MATCH ... ORDER BY rank`, returns top-N hits each
   with ±N messages of surrounding context + session bookends; an **LLM-summarizer
   condenses** the raw hits before they re-enter context (keeps the recall payload small —
   the tool returns a digest, not a transcript dump).

---

## Schema DDL

Adapted from Hermes `state.db` (SCHEMA_VERSION 16), trimmed to Alfred's MVP. Alfred-owned
columns are pydantic-schema-first (CLAUDE.md: schema before behavior); a `schema_version`
table drives forward migrations.

```sql
-- ---- sessions: one row per conversation -----------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    id                  TEXT PRIMARY KEY,          -- uuid/ulid
    source              TEXT NOT NULL,             -- 'cli' | 'server' | 'cron' | 'subagent'
    model               TEXT,                      -- resolved model id at session_start
    model_config        TEXT,                      -- JSON: frozen AgentConfig.model subtree
    system_prompt       TEXT,                      -- frozen prefix (Decision #21/#29)
    title               TEXT,                      -- lazily LLM-generated short title
    parent_session_id   TEXT,                      -- subagent / resume-branch / compaction chain
    started_at          REAL NOT NULL,             -- epoch seconds
    ended_at            REAL,
    end_reason          TEXT,                      -- 'normal' | 'compression' | 'branched' | 'error'
    message_count       INTEGER DEFAULT 0,
    tool_call_count     INTEGER DEFAULT 0,
    input_tokens        INTEGER DEFAULT 0,
    output_tokens       INTEGER DEFAULT 0,
    cache_read_tokens   INTEGER DEFAULT 0,         -- feeds e2e #17 cache assertion
    cache_write_tokens  INTEGER DEFAULT 0,
    reasoning_tokens    INTEGER DEFAULT 0,
    FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_started   ON sessions(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sessions_parent    ON sessions(parent_session_id);

-- ---- messages: the conversation record (SSoT) -----------------------------
CREATE TABLE IF NOT EXISTS messages (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT NOT NULL REFERENCES sessions(id),
    seq               INTEGER NOT NULL,            -- monotonic per-session ordering
    role              TEXT NOT NULL,               -- 'user' | 'assistant' | 'tool' | 'system'
    content           TEXT,                        -- visible text (what FTS indexes)
    tool_calls        TEXT,                        -- JSON array: assistant-requested calls
    tool_call_id      TEXT,                        -- correlates a 'tool' row to its request
    tool_name         TEXT,
    reasoning         TEXT,                        -- visible/redacted reasoning summary
    reasoning_content TEXT,                        -- raw thinking block (vendor-specific)
    reasoning_details TEXT,                        -- JSON: signatures/encrypted blocks for replay
    finish_reason     TEXT,
    token_count       INTEGER,
    timestamp         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

-- ---- FTS5 full-text index (external content over messages.content) ---------
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=id,
    tokenize='porter unicode61'      -- stemming: "corrected" matches "correct"
);

-- ---- triggers keep FTS in sync (Hermes pattern; FTS5 best practice) --------
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.id, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
```

Notes:
- `seq` (per-session monotonic) gives deterministic replay ordering independent of the
  global autoincrement `id`; `ORDER BY seq` reconstructs a turn even if rows interleave
  across sessions in the table. Hermes leans on `id` ordering; `seq` is the Alfred
  hardening so subagent rows (different session) never confuse parent ordering.
- Reasoning columns are nullable — the porter-stemmed FTS indexes only `content` (visible
  text). Indexing reasoning is an open question (noise vs. recall, see below).
- `model_config` + `system_prompt` are frozen at `session_start` (Decision #21/#29). On
  resume, replaying the *same* frozen prefix is what makes the prompt cache hit (e2e #17).

---

## Interface sketch

Schema-first pydantic types are the contract; SQLite is the swappable impl behind it.

```python
# stores/session/types.py  — Alfred-owned, provider-agnostic
class Message(BaseModel):
    role: Literal["user", "assistant", "tool", "system"]
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    tool_name: str | None = None
    reasoning: str | None = None             # separated from visible content
    reasoning_content: str | None = None
    reasoning_details: dict | None = None
    finish_reason: str | None = None
    token_count: int | None = None

class SearchHit(BaseModel):
    session_id: str
    message_id: int
    role: str
    timestamp: float
    snippet: str                              # FTS5 snippet() with >>>match<<< markers
    context: list[Message]                    # ±N around the hit
    session_title: str | None

# stores/session/base.py  — the Ring-2 interface (swappable)
class SessionStore(ABC):
    @abstractmethod
    def create_session(self, *, source: str, model: str,
                       model_config: dict, system_prompt: str,
                       parent_session_id: str | None = None) -> str: ...

    @abstractmethod
    def add_message(self, session_id: str, msg: Message) -> int:
        """Single write txn (BEGIN IMMEDIATE + jitter retry). Returns row id.
           Only COMPLETE messages — never stream_delta."""

    @abstractmethod
    def get_messages(self, session_id: str,
                     *, include_chain: bool = True) -> list[Message]:
        """Rehydrate full history in `seq` order; follows parent chain
           for compaction continuations when include_chain."""

    @abstractmethod
    def latest_session(self, *, source: str | None = None) -> str | None:
        """Most-recent session id → powers `--continue`."""

    @abstractmethod
    def list_sessions(self, *, limit: int = 20) -> list[SessionMeta]:
        """Picker rows → powers `--resume` selection UI."""

    @abstractmethod
    def search(self, query: str, *, limit: int = 5,
               context_radius: int = 5) -> list[SearchHit]:
        """FTS5 MATCH ... ORDER BY rank → powers session_search tool."""

    @abstractmethod
    def end_session(self, session_id: str, *, reason: str) -> None: ...
```

`--continue` vs `--resume` (both rehydrate via the same `get_messages`):

| Verb | Session selection | Recap behavior |
|---|---|---|
| `--continue` | `latest_session(source=...)` — newest implicitly | replay full history verbatim if it fits budget; recap only if over-budget / compressed chain |
| `--resume [id]` | explicit id, or `list_sessions()` picker | same rehydrate path; recap shown to *user* as orientation regardless (you chose to come back to an old thread) |

**Recap-before-resume**: a recap is an LLM summary of the rehydrated history. It is NOT a
substitute for the message record (which stays the SSoT) — it's (a) a budget-fit fallback
when full replay won't fit, injected as a single synthetic system/assistant message at
`session_start`; (b) a user-facing orientation line on `--resume`. When full history fits,
prefer verbatim replay so the frozen prefix cache-hits (Decision #29 / e2e #17).

---

## Concurrency

Topology: `agentkit-server` daemon (cron/dream/SSE host, Decision #6) + one or more
`agentkit-cli` / in-process SDK processes, all opening the same `sessions.db`. WAL is the
right primitive: **readers never block the writer, the writer never blocks readers**;
cross-process coordination is via SQLite's file locks.

Adopted parameters (Hermes-verified, see refs):

| Concern | Setting | Why |
|---|---|---|
| Journal mode | `PRAGMA journal_mode=WAL` (fallback `DELETE`) | concurrent readers + 1 writer. WAL **fails on NFS/SMB/FUSE** (`locking protocol`) → fall back to DELETE with a one-time WARNING, never crash silently (Fail-Loud: surface the cause to `--resume`). |
| Busy timeout | `timeout=1.0` (1s, not default) | keep contention out of SQLite's internal busy handler; let *application* retry handle it so we control the backoff curve. |
| Sync | `PRAGMA synchronous=NORMAL` | safe under WAL, big write throughput win vs FULL. |
| FK | `PRAGMA foreign_keys=ON` | parent_session_id chain integrity. |
| Isolation | `isolation_level=None` | Python sqlite3 auto-BEGIN conflicts with explicit `BEGIN IMMEDIATE`; manage txns manually. |
| Write txn | `BEGIN IMMEDIATE` | takes the write lock at txn *start*, not first write → surfaces contention immediately, avoids upgrade deadlocks between two would-be writers. |
| Retry | up to **15** attempts, sleep **uniform(20ms, 150ms)** on `locked`/`busy` | random jitter staggers competing writers and breaks the thundering-herd convoy that SQLite's deterministic internal backoff creates. Non-lock errors propagate immediately. |
| Checkpoint | `PRAGMA wal_checkpoint(TRUNCATE)` every **50** successful writes + on close | keeps WAL file from growing unbounded when many processes hold persistent connections; TRUNCATE (not PASSIVE) actually shrinks the file. Best-effort, never fatal. |

Write helper (the single funnel for all mutations):

```python
def _write(self, fn):
    for attempt in range(15):
        try:
            with self._lock:                      # in-process thread guard
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    result = fn(self._conn); self._conn.commit()
                except BaseException:
                    self._conn.rollback(); raise
            self._write_count += 1
            if self._write_count % 50 == 0:
                self._try_wal_checkpoint()        # TRUNCATE, best-effort
            return result
        except sqlite3.OperationalError as e:
            if ("locked" in str(e).lower() or "busy" in str(e).lower()) and attempt < 14:
                time.sleep(random.uniform(0.020, 0.150)); continue
            raise
```

`threading.Lock` guards intra-process threads; `BEGIN IMMEDIATE` + jitter retry guards
inter-process contention. Reads (`get_messages`, `search`, `list_sessions`) take no write
lock and proceed concurrently under WAL — including a `mode=ro` connection for the daemon
polling another process's live DB (sidebar/SSE) without ever contending.

**Daemon "session" boundary (Eng finding H3):** the long-lived daemon does NOT keep one
session open forever. Cron ticks use a *fresh session per tick* (Decision #6, Hermes cron
model → `source='cron'`); interactive daemon sessions are bounded by `end_session`. This
keeps the frozen-prefix/cache discipline intact and gives distill/dream a clean per-session
read boundary.

---

## Industry refs (URLs)

- Hermes Agent — Session Storage (verbatim schema, triggers, WAL params): https://hermes-agent.nousresearch.com/docs/developer-guide/session-storage
- Hermes Agent — `hermes_state.py` source (retry loop, BEGIN IMMEDIATE, checkpoint, WAL-fallback): https://github.com/NousResearch/hermes-agent/blob/main/hermes_state.py
- Hermes Agent — Sessions user guide (`/resume`, `/continue`, search behavior): https://hermes-agent.nousresearch.com/docs/user-guide/sessions
- Hermes Agent — Memory & Sessions (DeepWiki, session_search recap ±5 + bookends): https://deepwiki.com/NousResearch/hermes-agent/4.3-memory-and-sessions
- SQLite FTS5 official (external content, contentless tables, porter tokenizer, rank): https://sqlite.org/fts5.html
- SQLite WAL official (NFS/SMB busy caveat, journal modes): https://www.sqlite.org/wal.html
- "SQLite concurrent writes and database is locked" (BEGIN IMMEDIATE, busy_timeout): https://tenthousandmeters.com/blog/sqlite-concurrent-writes-and-database-is-locked-errors/
- Bert Hubert — SQLITE_BUSY despite timeout (why app-level retry beats internal handler): https://berthub.eu/articles/posts/a-brief-post-on-sqlite3-database-locked-despite-timeout/
- DOCSAID — WAL busy_timeout for workers (production tuning): https://docsaid.org/en/blog/sqlite-wal-busy-timeout-for-workers/
- SQLite Python + FTS5 + WAL tutorial (2026): https://tech-insider.org/sqlite-python-tutorial-fts5-wal-mode-2026/
- Spring AI Session API — event-sourced short-term memory, separating metadata from content (2026): https://spring.io/blog/2026/04/15/spring-ai-session-management/
- "7 State Persistence Strategies for Long-Running AI Agents" (checkpoint = full state incl. reasoning, 2026): https://www.indium.tech/blog/7-state-persistence-strategies-ai-agents-2026/

---

## Open questions

1. **Index reasoning in FTS?** Currently FTS indexes only visible `content`. Indexing
   `reasoning_content` could improve recall ("when did the agent consider X?") but adds
   noise and bloats the index. Lean: keep content-only for MVP; revisit if recall is weak.

2. **Tokenizer choice for code-heavy content.** `porter unicode61` stems English well but
   mangles code identifiers/symbols. Hermes additionally runs a `trigram` FTS for
   substring/code search (note the `messages_fts_trigram_*` triggers in its source). MVP =
   porter only; consider a second `fts5(tokenize='trigram')` table if `session_search` on
   code is poor.

3. **Cross-session search scope & privacy.** `session_search` over *all* sessions vs.
   filtering by `source`/`user_id`. MVP single-user → search-all. Multi-tenant (server
   path) would need a `user_id` filter on the FTS join.

4. **Recap trigger threshold.** Exact budget fraction at which `--continue` switches from
   verbatim replay to recap-injection. Must coordinate with kernel context-assembly
   (frozen prefix) — verbatim replay is cache-friendly, recap is not. Defer to the
   context+cache module; surface as a config knob.

5. **Compaction chains vs. trace store.** When a session is compressed (`end_reason=
   'compression'` → child session), the *summary* is a conversation artifact (session
   store) but the *discarded raw turns* may be learning material (trace store). Need a
   clean rule for what the compaction step writes where — coordinate with trace + dream
   modules. (See boundary section below.)

---

## Boundary with trace store (what goes where — no duplication)

This is the load-bearing SSoT split (Decision #17, §4.2). Same events feed both, but they
store **different projections for different consumers** — do not duplicate the message
body across both.

| | **Session store (this module)** | **Trace store (separate module)** |
|---|---|---|
| SSoT for | the conversation *record* | agent-*learning* raw material |
| Contents | complete messages, tool calls, tool results, reasoning, session metadata | annotated execution trajectories: success/failure labels, user-pushback/correction signals, off-track markers, tool i/o as *evaluable steps* |
| Written at | every complete message (turn-by-turn) | `pre/post_tool`, `turn_end` (Decision #17) — annotation-bearing |
| Read by | `get_messages` (resume), `session_search` tool (recall), recap summarizer | distill / evolve / dream (Decisions #17, #18, #18a) — never from session |
| NOT here | trace annotations, eval/replay sets, learning labels | the canonical replayable transcript; FTS recall index |

Rules:
- The loop writes the **message** to the session store and an **annotated trace step** to
  the trace store. They share the *event* (`post_tool`/`turn_end`) but not the *storage*.
- distill/evolve/dream read **only** the trace store. `session_search` / resume read
  **only** the session store. No subsystem crosses over.
- `stream_delta` lands in **neither** — it's a transient render projection (Decision #15).
- Memory facts land in **neither** — separate Ring-2 memory store (Decision #17a).
- Reference, not duplicate: a trace step may carry a *pointer* (`session_id` + message
  `id`) back to the session row rather than copying the message body — matching the
  kernel event payload rule "references + metadata only, not full bodies" (Decision #7).
