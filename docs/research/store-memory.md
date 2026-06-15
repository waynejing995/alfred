# Ring-2 Store — Memory (cross-session facts)

Date: 2026-06-15
Module: `agentkit/stores/memory/` (Ring-2, interface + default impl, swappable)
Spec refs: §4 (Stores table), §4.1 (Memory), Decisions #17a, #18 (dream split), #21/#29 (cache discipline), #17 (trace boundary); e2e #16 (dream tidies memory).

---

## Module scope

The memory store is the **SSoT for cross-session facts** — durable knowledge that
must survive across conversations (who the user is, their preferences, project state,
learned-context). It is NOT the conversation log (that is `session`) and NOT learning
raw-material (that is `trace`). See "Boundary" below — the three stores are disjoint.

In scope:
- Persist human-readable facts across sessions (default impl: markdown files).
- **Retrieval at session_start**: top-k relevant facts fused into the frozen prefix
  (Decision #17a: retrieval-based, NOT dump-everything).
- Agent-facing `memory_*` tools to write/edit facts mid-session (take effect next
  session — frozen-prefix discipline).
- A **swappable `MemoryProvider` interface** so mem0/Zep/Graphiti can be added later
  as A/B alternatives with zero kernel change. They are not MVP runtime dependencies.

Explicitly OUT of scope:
- Conversation messages → `session` store (§4, store-session.md).
- Trace/annotation learning material → `trace` store (§4.2).
- Memory housekeeping (dedup/merge/re-index/decay) → **dream subsystem** (Ring-3, S4,
  Decision #18). The store exposes the primitives; dream orchestrates them. The store
  does NOT self-consolidate.

**Design stance (CEO review warning honored): keep the MVP DUMB.** The contested point
in the field is the *ranker*, and ranking only pays off once you have real traces to
tune against. So the MVP ships the simplest readable file+retrieval that clears the
Letta filesystem baseline, and the *interface* (not the ranker) is where the
engineering investment goes — because the interface is what lets us swap in mem0/Zep
and actually measure whether a smarter ranker helps.

---

## Recommended MVP

The dumbest thing that hits the Letta "filesystem is all you need" baseline:
**markdown files on disk + SQLite FTS/entity/recency retrieval, no graph, no embedding
dependency, no learned ranker.**

### File layout

```
<memory_root>/                      # e.g. ~/.alfred/memory/  (configurable via ALFRED_HOME)
  core/                             # GLOBAL; ALWAYS injected verbatim (not retrieved)
    persona.md                      #   agent self-description
    user.md                         #   stable facts about the user (Letta "human" block)
  facts/                            # RETRIEVED top-k (the searchable corpus)
    <slug>.md                       #   one fact-cluster per file, frontmatter-described
  index/
    facts.db                        #   SQLite: FTS5 (BM25) + entity/recency/access metadata
```

`core/` is global and has no `project_id`. `facts/*.md` stay human-readable, but the
derived `index/facts.db` rows carry `project_id`; default retrieval always filters
`WHERE project_id=?` (current project only). Cross-project retrieval is a later widening
of that WHERE clause, not a second index.

Each `facts/<slug>.md` is a small, human-editable unit with SKILL.md-style frontmatter
(matches Decision #24's Claude-Code-compatible format, so it reads/exports trivially):

```markdown
---
id: fact-0042
summary: User prefers uv over pip; Python 3.12+
entities: [user, uv, pip, python]
created: 2026-06-10
updated: 2026-06-14
source_session: sess-118
---
The user manages all Python with `uv run python`, never `.venv/bin/python` or pip.
Confirmed across sessions 109, 118.
```

Why this layout (each choice is the dumb-but-correct one):
- **`core/` vs `facts/` split = the load-bearing decision, not the ranker.** Letta's
  working pattern is a tiny *always-present* block (persona + human) plus a large
  *retrieved-on-demand* archive. `core/` is small enough to inject whole (a few KB);
  `facts/` is the corpus you retrieve from. This split is what makes "retrieval, not
  dump" actually work — you never dump `facts/`, you only ever dump tiny `core/`.
- **One fact-cluster per file** (not one giant `MEMORY.md`, not one-row-per-fact DB):
  files are the human-readable SSoT (export-to-md is free — it IS md); the DB is a
  pure derived index, reconstructible by re-scanning files (SSoT discipline — DB can
  be deleted and rebuilt).
- **Frontmatter `summary`/`entities`** gives the retriever cheap signal without an LLM
  in the read path. `entities` is hand/dream-maintained, not extracted per-query.

### Retrieval (DUMB on purpose)

The MVP retriever uses only low-dependency signals available from files + SQLite:

1. **BM25** over file body + `summary` (SQLite FTS5 — already in the stack for session).
2. **Lexical summary overlap**: token/Jaccard overlap between the seed query and the
   frontmatter `summary`; cheap stand-in for a semantic pass without an embedding model.
3. **Entity overlap**: count query-entities ∩ file `entities` frontmatter.
4. **Recency/access**: optional rank from `updated`, `last_retrieved`, and `access_count`
   metadata so recently useful facts can surface without a learned model.

Fuse by **Reciprocal Rank Fusion (RRF)** — `score = Σ 1/(k + rank_i)` — NOT a tuned
weighted sum. RRF needs zero tuning and is rank-based, so the BM25, lexical, entity,
and recency passes do not need comparable score scales. **This is the whole MVP
ranker.** Do not build embeddings, sqlite-vec, graph traversal, or a learned reranker
for the default store. Those belong in future `MemoryProvider` adapters/A-B arms once
Alfred has real traces to measure lift.

Top-k: start `k=10` facts (~a few KB), config-capped by token budget. Tunable later
once real session traces show what k recall needs.

**The query** at session_start: there is no user turn yet, so seed the retrieval query
from (a) `core/user.md` + persona (stable context), (b) the goal text if a goal is set
(Decision #19 injects goal at session_start too), (c) on `--continue`, the last N
messages of the resumed session. Dumbest viable: concatenate these as the query string.

This clears the Letta filesystem baseline because it IS the Letta filesystem pattern
(readable files + retrieval) plus mem0's fusion retriever — both proven shapes, no
novel ranking risk.

---

## MemoryProvider interface

The swap surface. Kernel/loop never see files, indexes, vectors, or graphs — only this
ABC. mem0/Zep/Graphiti become alternate impls behind it (registered like any `{type,
params}` component,
Decision #13). Lifecycle is **three calls**, matching the agent loop's real shape:

```python
class RetrievedMemory(BaseModel):
    """What retrieval returns — provider-agnostic, ready to freeze into the prefix."""
    blocks: list[MemoryBlock]      # ordered; each = id + text + source + score
    query: str                     # the seed query used (for trace/debug)
    token_estimate: int            # so context-assembly can budget the prefix

class MemoryWrite(BaseModel):
    op: Literal["append", "replace", "delete"]
    target: str | None             # fact id for replace/delete; None = new
    text: str
    entities: list[str] = []

class MemoryProvider(ABC):
    @abstractmethod
    def prefetch(self, ctx: SessionContext) -> RetrievedMemory:
        """Called ONCE at session_start. Returns top-k to freeze into the prefix.
        ctx carries the seed-query material (persona/user/goal/resumed-tail).
        MUST be synchronous-complete before prompt assembly (it's on the cache path)."""

    @abstractmethod
    def sync_turn(self, writes: list[MemoryWrite], ctx: TurnContext) -> None:
        """Called at turn_end (async, non-blocking). Persists this turn's memory_* tool
        writes to the durable store. Does NOT mutate the live prefix (see Retrieval+freeze).
        Default impl: write files + mark index dirty. mem0/Zep: call their add() API."""

    @abstractmethod
    def shutdown(self) -> None:
        """Called at session_end / daemon stop. Flush, fsync, close index handles.
        Default impl: checkpoint SQLite, flush pending entity re-index."""

    # --- optional capability, not all providers implement ---
    def search(self, query: str, k: int = 10) -> RetrievedMemory:
        """On-demand mid-session retrieval, exposed as the `memory_search` tool.
        Lets the agent pull more facts WITHOUT mutating the frozen prefix."""
```

### Lifecycle mapping (push, not poll — Decision #5)

| Kernel event | Provider call | Blocking? |
|---|---|---|
| `session_start` | `prefetch()` | **sync** — result is part of the frozen prefix |
| `turn_end` | `sync_turn(writes)` | async — background-trigger event |
| `session_end` | `shutdown()` | async |

No polling loop. The store reacts to lifecycle events the kernel already emits.

### Retrieval modes (how a provider surfaces memory)

Three modes, NOT mutually exclusive — a provider declares which it supports; config
picks the active set:

| Mode | Mechanism | Provider support |
|---|---|---|
| **context** | `prefetch()` freezes top-k into session_start prefix (passive, always-on) | all (the MVP default) |
| **tools** | agent calls `memory_search` / `memory_*` write tools on demand (active) | all (default impl + mem0; Letta-native style) |
| **hybrid** | both: small frozen seed in prefix **+** tool for deeper pulls | all; recommended default |

`hybrid` is the recommended default: a cheap always-present seed (covers the common
case, cache-friendly) plus an escape hatch tool for the long tail — without re-freezing
the prefix. This is exactly how Letta works (small core block + archival search tool).

### Why these three calls (not more)

`prefetch/sync_turn/shutdown` is the minimum that covers read-at-start, write-at-end,
flush-at-stop. mem0's `search()`+`add()` and Zep's graph query/ingest both collapse
cleanly onto it (`add` → `sync_turn`, `search` → `prefetch`/`search`). No
`consolidate()` method — consolidation is **dream's** job (Ring-3), invoked via the
store's own primitives, not a provider lifecycle hook. Keeping consolidation out of the
provider interface is what keeps the boundary clean: a provider only reads and writes;
it never decides *when* to tidy.

---

## Retrieval + freeze

This is where memory meets the Ring-1 cache discipline (Decision #21, #29) — the
critical correctness boundary.

1. **`prefetch()` runs once, at session_start, before prompt assembly.** Its
   `RetrievedMemory.blocks` are concatenated into the **static system prefix** —
   alongside persona, project instructions, and skill L0 index — and the cache breakpoint
   (`cache_control: ephemeral` for Anthropic; prefix-stability for OpenAI) is placed
   *after* them. From that point the retrieved memory is **frozen for the session**.

2. **`core/` blocks** (persona + user) are injected verbatim, always, ahead of the
   retrieved `facts/` blocks. Order within the prefix is fixed
   (`persona → user → project_instructions → retrieved facts → skill L0`) so the prefix is byte-stable across
   turns → cache hits.

3. **Mid-session writes do NOT mutate the prefix.** `memory_*` tools and `sync_turn()`
   write to the *durable store*, not the live prompt. If they edited the prefix mid-
   session the cache breakpoint would invalidate every turn (the 10× cost failure
   #29 fails loud on). New/edited facts therefore take effect **next session** (next
   `prefetch`). This is identical to the skill-freeze rule (Decision #12) — same cache
   economics, same mental model.

4. **`memory_search` tool results are turn-local**, appended as a tool-result message
   *after* the cache breakpoint (in the mutable suffix), never folded back into the
   frozen prefix. So the agent can pull more facts on demand without breaking cache.

5. **Daemon "session" caveat (Eng H3):** in a long-lived `agentkit-server` daemon,
   "next session" = next fresh-session boundary (cron tick = fresh session; interactive
   daemon = explicit reload trigger). Memory re-`prefetch` happens at that boundary, not
   continuously. Same reload semantics as skills/config.

**Verification tie-in:** e2e #17 (cache-hit > 0 on turn-2) implicitly guards this — if a
`memory_*` write leaked into the prefix mid-session, turn-2 `cached_tokens` would drop
and the runtime WARNING (#29) would fire.

---

## Memory writes (agent `memory_*` tools)

The agent edits its own memory through tools, Letta-style. Minimal verb set
(`core_memory_append`/`core_memory_replace` are Letta's proven primitives; we keep the
same two shapes plus a fact-level pair):

| Tool | Effect | Target |
|---|---|---|
| `memory_append` | add a new fact (new `facts/<slug>.md`) or append to a core block | facts / core |
| `memory_replace` | replace a fact's text or a core block's content | facts / core |
| `memory_search` | on-demand retrieval (the `tools` mode escape hatch) | read-only |

(`memory_delete` is **not** an agent tool in MVP — deletion/decay is dream's job, and
an agent that can hard-delete its own facts is a footgun. Agents append/replace;
forgetting is a supervised background process.)

**When writes take effect (the key invariant):**
- The *write to durable store* happens at `turn_end` via `sync_turn()` — durable
  immediately.
- The *effect on what's injected* happens at the **next session/epoch** `prefetch()` —
  because the prefix is frozen (above). An agent that writes a fact in turn 3 will not
  see it in its own prefix this session; it will next session. (If it needs it this
  session, it uses `memory_search`, which reads the durable store live.)

This append-now / inject-next-session split is the same frozen-snapshot discipline as
skills and config (#12, #24) — one consistent rule across all three Ring-2 frozen
surfaces, no special case.

**dream interaction (Decision #18):** dream reads session+trace at `idle`/`session_end`
and performs dedup/merge/re-index/decay over `facts/` using the store's primitives
(merge two slugs, rewrite frontmatter `entities`, decay stale facts). dream is the ONLY
writer that deletes/merges. This mirrors Letta's sleep-time reflection +
defragmentation (background subagent that splits/merges files into a clean 15–25-file
hierarchy). e2e #16 verifies dream tidies memory and leaves skills untouched.

---

## Boundary

Three Ring-2 stores, **disjoint by content type — no overlap** (Decisions #17, #17a):

| Store | Content | Question it answers | Written by | Read by |
|---|---|---|---|---|
| **session** | conversation messages (verbatim) | "what was said?" | loop (every complete msg) | `--continue`, `session_search`, dream (as input) |
| **memory** | distilled cross-session **facts** | "what do I know?" | agent `memory_*` tools, dream | `prefetch` → prefix, `memory_search` |
| **trace** | annotated execution **trajectories** (success/failure/pushback/off-track) | "how did I do / what should I learn?" | loop at pre/post_tool, turn_end | distill, evolve, dream |

The clean separations:
- **memory ≠ session.** Session is the raw verbatim transcript (replayable, lossless).
  Memory is the *extracted, deduplicated, durable* facts (lossy, curated). A fact in
  memory may summarize 5 sessions; the sessions stay in `session` untouched. dream is
  the bridge: it *reads* session, *writes* memory — but they are different stores with
  different SSoT. Never store conversation in memory; never store distilled facts in
  session.
- **memory ≠ trace.** Trace is learning *raw material* (what tools ran, where it went
  off-track, where the user pushed back) consumed by distill/evolve to make *skills*.
  Memory is *facts about the world/user* consumed by the loop to *answer better*. A
  pushback signal goes to trace (→ evolve a skill); a fact the user stated goes to
  memory (→ recall it). They are never the same record.
- **The dream split (Decision #18):** dream touches **memory only**, never skills.
  distill/evolve touch **skills** (via trace), never memory. One subsystem per store-
  class; no mutual calls. This is what keeps "housekeeping" from becoming a god-object.

Overlap test (must always be answerable): given any new piece of state, exactly one
store owns it — transcript→session, fact→memory, trajectory-annotation→trace. If two
answers exist, the boundary is broken.

---

## Industry refs (with URLs)

- **Letta — filesystem / context-repository memory** (the MVP reference): files with
  SKILL.md-style frontmatter, `system/` always-loaded vs retrieved tree, grep/bash/read
  tools over a local clone, sleep-time reflection + defragmentation (split/merge into
  15–25 focused files), git-versioned writes. This is the exact shape of the MVP.
  https://www.letta.com/blog/context-repositories/
- **Letta — memory blocks (core memory)**: labeled persistent blocks the agent edits
  with `core_memory_append`/`core_memory_replace`; conventional blocks = human + persona;
  archival = on-demand searchable. Source of our `core/` split + write-tool verbs.
  https://docs.letta.com/guides/core-concepts/memory/memory-blocks
  https://docs.letta.com/guides/core-concepts/memory/archival-memory
- **Letta — sleep-time agents**: background process updates memory blocks during idle;
  "learned context." Maps onto Alfred's dream subsystem.
  https://docs.letta.com/guides/agents/architectures/sleeptime/
- **mem0 — architecture + 2026 benchmarks**: single-pass ADD-only extraction and
  multi-signal retrieval. Its vector/semantic pass is the A/B vector alternative, not
  the MVP default. Alfred borrows the multi-signal + RRF shape while keeping the default
  implementation dependency-light (BM25 + lexical/entity/recency). Best-practices:
  async writes, optional second-pass reranker, metadata filtering.
  https://mem0.ai/blog/state-of-ai-agent-memory-2026
  https://mem0.ai/research
  https://arxiv.org/html/2504.19413v1
- **Zep / Graphiti — temporal knowledge graph**: bi-temporal model (t_valid/t_invalid
  per edge), fusion of time+full-text+semantic+graph query; strong on temporal/open-
  domain; the A/B temporal-KG alternative. (LoCoMo numbers are methodology-contested —
  Zep ~75–85%, see note below.)
  https://arxiv.org/abs/2501.13956
  https://github.com/getzep/graphiti
  https://help.getzep.com/graphiti/getting-started/overview
- **2026 comparisons** (architecture + scores; treat scores as directional):
  https://dev.to/varun_pratapbhardwaj_b13/5-ai-agent-memory-systems-compared-mem0-zep-letta-supermemory-superlocalmemory-2026-benchmark-59p3
  https://blog.devgenius.io/ai-agent-memory-systems-in-2026-mem0-zep-hindsight-memvid-and-everything-in-between-compared-96e35b818da8

**Note on LoCoMo numbers:** the field's scores are contested (different harnesses,
disputed "adversarial category" inclusions — Zep claimed 84%, mem0 recomputed 58%, Zep
counter-claimed 75%; Letta filesystem cited ~74–83% depending on source). Do **not**
hard-code a target number. The point of the spec's #17a baseline is *the architecture*
(readable files + hybrid retrieval), not chasing a contested digit. Alfred's own eval
harness (module #17) is the only number we should trust — measure default-impl vs
mem0 vs Zep *on our own traces*, which is precisely why the swap interface exists.

---

## Open questions

1. **Future semantic adapter.** When real traces justify it, add an optional
   embedding-backed adapter or ranking pass (local model vs provider embeddings) behind
   `MemoryProvider`. It must not enter the default files implementation until eval shows
   a measurable lift over BM25+lexical+entity+recency.
2. **Seed-query quality at session_start.** With no user turn yet, the seed is
   persona+user+goal+resumed-tail. Is that enough recall, or do we need a cheap "what's
   relevant now?" pre-pass? Defer — measure recall on real traces first (CEO: don't
   pre-build the ranker).
3. **Entity extraction ownership.** Frontmatter `entities` is currently
   hand/dream-maintained. Should `sync_turn` auto-extract entities on write (needs an
   LLM in the write path, latency) or leave it to dream (batch, cheap)? MVP: dream does
   it (keeps the write path dumb).
4. **`prefetch` latency on the cache path.** It's synchronous before prompt assembly. If
   the index grows large, does retrieval add user-visible startup latency? Bound k and
   index size; keep FTS/entity/recency lookup pure SQLite in the default path. Semantic
   adapters must precompute their own expensive derived data at write/batch time.
5. **Decay policy for dream.** What makes a fact "stale" — age, last-retrieved, contradiction
   by a newer fact? Out of scope for *this* module (it's dream's policy, Decision #18),
   but the store must expose `last_retrieved` / `updated` metadata so dream can decide.
   Flagged here so dream's research picks it up.
6. **Multi-process write safety.** Daemon (dream) + CLI (agent `memory_*`) may both write
   `facts/` + index concurrently. Reuse session-store's SQLite WAL + BEGIN IMMEDIATE
   retry discipline for `index/facts.db`; file writes need an atomic-rename + per-fact
   lock (mirrors skill-store M4). Confirm against dream's batch-merge transaction shape.
```
