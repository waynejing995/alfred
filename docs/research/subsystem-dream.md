# Ring-3 Subsystem — dream (memory housekeeping)

Date: 2026-06-15
Module: `agentkit/subsystems/dream/` (Ring-3, plugin, event-driven, disable-able, A/B-able)
Spec refs: §5.3 (Ring-3 table), Decision #18 (dream vs distill hard split), #18 (dream = memory housekeeping ONLY), #20c (governed by global autonomy), #5/#7 (events), #17a/§4.1 (memory store), e2e #16 (dream tidies memory, skills untouched).
Peer/dependency docs: `store-memory.md` (the store dream operates on — defines the file layout, metadata, and the rule "dream is the ONLY merger/deleter"), `subsystem-distill.md` (peer; hard split), `store-trace.md` / `store-session.md` (dream's read inputs).

---

## Module scope

dream is the **memory janitor**. It reads `session` + `trace`, and the ONLY thing it
writes is the `memory` store's `facts/` corpus + its derived index. Four operations,
nothing else:

1. **dedup** — collapse near-duplicate facts.
2. **merge** — combine related/overlapping facts into one cleaner fact.
3. **re-index** — rebuild the derived retrieval index (FTS5 + entity/recency/access metadata)
   after writes; refresh frontmatter `entities`.
4. **forgetting/decay** — age out stale facts (archive, not hard-delete in MVP).

In scope:
- Subscribe `idle` and `session_end` (async, background-trigger — never blocks the loop).
- Run a consolidation pass over recently-touched facts (a batch), call a model via the
  injected `ModelProvider` for the LLM-judged merge/dedup decisions.
- Be the **sole merger/deleter** of memory facts (the memory store, by design, never
  self-consolidates and the agent's `memory_*` tools can only append/replace — see
  store-memory.md). This is what makes dream the SSoT writer for *destructive* memory ops.
- Emit `dream.consolidated` (its own namespaced event) when a pass finishes — distill MAY
  listen, but dream never calls distill (Decision #18).
- Be governed by the global `autonomy` gate (#20c): `off` → no pass; `assist` → propose a
  diff, require confirmation before applying; `auto` → apply directly.

Explicitly OUT of scope (the emphatic boundary, Decision #18):
- **Skills.** dream NEVER reads or writes the skill store, never proposes/mutates a skill.
  trace→skill is distill's job; skill→variant is evolve's job. dream and distill are
  **peers with no mutual calls** — they coordinate only via plugin events.
- **Conversation messages** (session is read-only input; dream never edits session).
- **Fact extraction from a single turn** — that's the agent's `memory_*` tools at
  `turn_end` (store-memory.md). dream operates on facts that already exist; it tidies the
  accreted corpus, it does not do per-turn capture.
- **Retrieval ranking** — that's the store's `prefetch()`. dream maintains the index
  dream wrote; it does not own the ranker.

One-line boundary: **distill makes skills from traces; dream makes memory cleaner.** Same
`idle` hook, disjoint outputs, zero coupling.

---

## Consolidation algorithms

All four are deliberately the *dumb-but-correct* shape. The expensive, contested part of
the field is the per-query retrieval *ranker* (store-memory.md owns that, and CEO warned
off building it pre-trace). dream's job is housekeeping, which is well-served by simple,
threshold-driven, batch operations over the low-dependency memory index. No embedding
dependency, no graph service, no learned models, no sleep-cycle metaphor machinery in MVP.

### Candidate selection (shared by all four — keeps the pass cheap)

A full O(N²) pairwise scan of every fact every pass does not scale and is wasted work.
Bound the batch: dream only considers the **dirty set** — facts created/updated since the
last dream pass (tracked by `updated` timestamp + a `last_dream_pass` watermark stored in
`index/`). For each dirty fact, pull candidate neighbors via the *existing* low-dependency
index: shared entities, FTS/BM25 similarity over body+summary, lexical summary overlap,
same source/session neighborhood, and recency/access metadata. This is candidate pruning,
not a semantic ANN dependency.

```
dirty = facts where updated > last_dream_pass
for f in dirty:
    neighbors = index.related(f, k=8)  # FTS/entity/summary/recency signals, no embeddings
    for g in neighbors:
        consider_pair(f, g)            # → dedup or merge below
```

### 1. dedup (near-duplicate fact detection)

Two-tier, cheapest-first:

- **Tier A — deterministic lexical/entity checks.** Candidate pairs with the same primary
  entities and near-identical normalized summaries/bodies (token overlap, MinHash-style
  shingles, or exact normalized sentence match) are auto-deduped: keep the one with newer
  `updated` / more sources; fold the other into provenance. No LLM needed.
- **Tier B — LLM judge** for ambiguous candidates only. One batched prompt: "Are these
  two facts saying the same thing? If yes, return the single best merged statement; if no,
  NOOP." This mirrors mem0's ADD/UPDATE/DELETE/**NOOP** decision but applied *post-hoc in
  batch* over the existing store, not at write time.

Thresholds are **config, not hard-coded** because lexical overlap differs by corpus.
Semantic cosine thresholds are a future adapter concern, not the default dream path.

### 2. merge (combining related facts)

Merge ≠ dedup. Dedup removes redundancy (same fact twice); merge *composes* (several
partial facts → one richer fact). Trigger: a cluster of facts sharing entities with
moderate BM25/summary overlap OR a chain of `append`-style facts about the same target.

- Group the dirty set's neighbors into small clusters (connected components over the
  "merge-candidate" edges, or just the per-fact neighbor bucket — MVP: per-bucket, no
  global clustering).
- For each cluster (cap size, e.g. ≤5 facts), one LLM call: "Combine these facts about
  `<entities>` into one coherent fact; preserve every distinct claim; keep it concise;
  cite all source sessions." Output a single new `facts/<slug>.md` whose frontmatter
  `source_session` is the union, `created` = min, `updated` = now.
- The merged fact **supersedes** its inputs: inputs are archived (decay/forgetting path
  below), not silently destroyed — so a bad merge is reversible (Fail-Loud + revert
  discipline, mirrors skill versioning #20a).

This is exactly Stanford generative-agents *reflection* / Letta sleep-time
*defragmentation* (split/merge into a clean 15–25-file hierarchy), scoped to memory only.

### 3. re-index (rebuild the derived retrieval index)

The index (`index/facts.db`: FTS5 + entity/recency/access metadata) is a **pure derived view**
of `facts/*.md` — reconstructible by re-scanning files (store-memory.md SSoT rule). dream
is the natural place to keep it honest because dream is the batch writer.

- **Incremental (default, every pass):** for every fact dream added/merged/archived this
  pass, upsert/delete its FTS row, refresh entity rows and access/recency metadata, and
  refresh `entities` when needed (one cheap LLM/NER pass on changed bodies only — this
  is where dream owns entity extraction, keeping the per-turn write path dumb;
  store-memory.md open-Q #3).
- **Full rebuild (rare / repair):** drop and rescan all files. Triggered on a detected
  index/file divergence (fact count mismatch) or an explicit `dream --reindex`. Because
  the index is derived, a full rebuild is always safe — that's the SSoT payoff.

re-index is mechanical, no judgment — it runs *after* dedup/merge/decay have settled the
file set in a pass, so the index reflects the final state once per pass (not per edit).

### 4. forgetting / decay (age out stale memory)

Recency vs access-frequency vs relevance — the answer from 2026 practice is **a small
weighted score combining all three**, not picking one. This is the Stanford
recency×importance×relevance retrieval score repurposed as a *retention* score, plus the
Ebbinghaus/FSFM access-frequency reinforcement.

Per fact, dream computes a **retention score**:

```
retention = w_r * recency        # exp decay on age since last_retrieved
          + w_f * frequency       # log(1 + access_count), reinforced on each retrieval
          + w_v * relevance       # similarity to current core/user + active goal
          - w_c * contradiction   # newer fact contradicts/supersedes this one
```

- **recency** = `exp(-λ * days_since_last_retrieved)` (Ebbinghaus exponential; generative-
  agents used decay 0.995/step). `last_retrieved` is the load-bearing field — store-memory
  already exposes it (open-Q #5 hands this to dream).
- **frequency** = access reinforcement: every time `prefetch`/`memory_search` surfaces a
  fact, the store bumps its `access_count` + `last_retrieved`. Useful facts naturally
  survive; never-retrieved facts decay. (Store must record the hit — small change, flagged
  to store-memory as the reciprocal of open-Q #5.)
- **relevance** = BM25/summary/entity overlap against the stable `core/` (persona+user)
  + goal text — a fact about a long-abandoned topic scores low. Semantic cosine is a
  future adapter signal, not required by MVP.
- **contradiction** = if a newer fact supersedes this one (detected during dedup/merge),
  push the old one toward archive (active supersession, the strongest forgetting signal).

**Action by score (archive, don't hard-delete — MVP):**
- `retention < forget_threshold` AND `days_since_last_retrieved > ttl_floor` → **archive**
  the fact: move `facts/<slug>.md` → `facts/.archive/<slug>.md` (dot-prefixed, glob-
  ignored by the retriever, exactly like skills' `.versions/`). It leaves the live corpus
  and the index, but is recoverable. Hard purge of `.archive/` is a separate, explicit,
  much-later operation — never automatic in MVP.
- otherwise → keep.

Why archive not delete: forgetting bugs are expensive and silent (you can't tell you lost
a fact). Archiving is Fail-Loud-friendly (the fact is visibly *moved*, recoverable) and
reuses the exact "dot-prefixed dir invisible to the scanner" pattern already proven for
skill versions (#20a-i). Weights `w_*` and thresholds are config; **sane defaults that
rarely fire** — decay is low-priority (CEO: dream only pays off once cruft accretes), so
default thresholds should be conservative (forget only clearly-dead facts).

---

## Trigger + batch

### Triggers (push, not poll — Decision #5)

dream subscribes two kernel events, both **async background-trigger** (never blocks the
loop; can run headless in the `agentkit-server` daemon per Decision #6):

| Event | Why | Behavior |
|---|---|---|
| `session_end` | a session just finished → new facts likely landed → good moment to tidy what that session touched | run a **scoped pass** over the facts written/updated during that session (small dirty set) |
| `idle` | no user activity → free compute (sleep-time-compute thesis) → safe to do the heavier full-corpus-ish pass | run a **batch pass** over the accumulated dirty set since `last_dream_pass` |

This is the sleep-time-compute pattern (Letta sleeptime agents): shift housekeeping to
when the system would otherwise sit idle, so it never adds user-visible latency. dream is
NOT a `while True: sleep(N)` loop — the scheduler/idle detector already emits these
events; dream just reacts.

**Debounce / re-entrancy (Eng-review-style runtime concern):** a pass must be guarded so
two passes don't run concurrently (idle fires while a session_end pass is mid-flight). MVP:
a single in-process async lock + a `dream_running` flag; if a trigger fires while a pass
runs, coalesce (set a `rerun_requested` bit, run once more after). In the daemon, the same
single-owner discipline as the memory index writer (store-memory open-Q #6 → reuse SQLite
WAL `BEGIN IMMEDIATE` for index writes; atomic-rename for file moves).

### Batch processing flow

```
on idle | session_end:
  if autonomy == off: return                       # global e-stop (#20c)
  if dream_running: rerun_requested = True; return  # coalesce
  dream_running = True
  try:
    dirty = facts updated since last_dream_pass      # bound the work
    if not dirty: return                             # nothing to do (common — cheap)
    pairs = [(f, g) for f in dirty for g in index.related(f, k)]   # candidates only
    # 1. dedup: auto (cos≥.95) + LLM-judge gray band
    # 2. merge: cluster related → 1 LLM call per cluster → new fact, archive inputs
    # 3. decay: retention score → archive dead facts
    # 4. re-index: upsert/delete changed rows, refresh entities (once, at end)
    diff = collected changes (merges, dedups, archives)
    if autonomy == assist:
        propose(diff); await confirm; if rejected: rollback; return
    apply(diff) atomically                           # WAL txn + atomic file renames
    last_dream_pass = now
    emit dream.consolidated(stats=...)               # distill MAY listen; dream does not call it
  finally:
    dream_running = False
    if rerun_requested: rerun_requested = False; schedule one more pass
```

**Daemon "session" boundary (Eng H3):** because the memory prefix is frozen per session
(store-memory.md retrieval+freeze), dream's writes do NOT change a *live* session — they
take effect at the **next** `prefetch` (next fresh session / next cron tick / explicit
reload). So `session_end` is the ideal trigger: the session whose facts we're tidying is
already over; its successor picks up the cleaned corpus. e2e #16 observes exactly this:
run sessions → run dream → memory file visibly tidied next time it's read.

### Cost discipline (CEO: low priority, don't over-engineer)

- A pass with an empty dirty set returns immediately (no LLM call) — the common case.
- LLM calls only in the dedup gray band and the merge step, batched, capped per pass.
- Decay is pure arithmetic over metadata (no LLM).
- Default thresholds make passes mostly no-ops until the corpus actually has cruft —
  which is the whole point: dream earns its keep only once memory accretes redundancy.

---

## Memory-only boundary

The hard wall (Decision #18, emphatic). Three mechanisms keep dream memory-only:

1. **No skill-store handle.** dream is constructed with the `MemoryProvider` + read access
   to `session`/`trace` + an injected `ModelProvider`. It is **not** given the skill loader
   or skill store. It *structurally cannot* touch skills — the dependency simply isn't
   wired. (Same DI discipline that keeps fusion from calling the loop, #11.)
2. **dream and distill are peers, no mutual calls.** Both may subscribe `idle`; they do
   disjoint work and never invoke each other. Coordination is event-only: dream emits
   `dream.consolidated`; distill MAY subscribe; neither imports the other. This is the
   "no god-object" guarantee — folding trace→skill into dream was explicitly rejected as
   duty pollution.
3. **e2e #16 is the executable proof:** after a dream pass, memory is tidied AND **skills
   are untouched** (byte-identical skill files before/after). The "skills untouched" half
   is the regression that fails loud if dream ever oversteps.

The clean boundary with the **memory store** (the reciprocal of store-memory.md's design):

| Concern | memory store owns | dream owns |
|---|---|---|
| read at session_start | `prefetch()` (frozen into prefix) | — |
| per-turn append/replace | agent `memory_*` tools → `sync_turn()` | — |
| **merge / dedup / delete-archive** | exposes primitives, **never self-consolidates** | **sole orchestrator** |
| index correctness | derived view; can be rebuilt | rebuilds it (incremental + repair) |
| entity frontmatter | stored, not extracted on write | extracts in batch (keeps write path dumb) |
| retrieval ranker | owns it (`prefetch`) | does not touch it |

The store deliberately has **no `consolidate()` lifecycle method** (store-memory.md "why
three calls" §) — consolidation lives in dream, invoked through the store's read/write
primitives (`MemoryWrite{op: replace|delete}`, file moves, index upsert). So:

- The store is the SSoT for *what facts exist and how they're retrieved*.
- dream is the SSoT for *destructive/consolidating mutations* (the only merger/deleter).
- The agent's `memory_*` tools are append/replace only — no `memory_delete` tool exists
  (store-memory.md): an agent hard-deleting its own facts is a footgun; **forgetting is a
  supervised background process = dream**. This is the cleanest possible split: additive
  writes are cheap and unsupervised; subtractive writes are batched, judged, archived (not
  destroyed), and gated by autonomy.

---

## Simple MVP

CEO review: dream is **low priority**, valuable only once memory has cruft — do NOT build
the sleep-cycle metaphor. The MVP is the smallest thing that passes e2e #16.

**Build (MVP):**
- Subscribe `idle` + `session_end`; async, coalesced, autonomy-gated.
- Dirty-set candidate selection via the store's existing FTS/entity/summary/recency index
  (no new infra, no embeddings).
- dedup: deterministic lexical/entity auto-band + one batched LLM-judge call for the
  ambiguous band.
- merge: per-neighbor-bucket cluster → one LLM call → new fact, archive inputs.
- decay: arithmetic retention score → archive (move to `.archive/`) clearly-dead facts;
  conservative default thresholds so it rarely fires.
- re-index: incremental upsert/delete + entity refresh at end of pass; full-rebuild as a
  manual repair command.
- emit `dream.consolidated`.
- Apply atomically (WAL txn + atomic renames); `assist` mode proposes a diff first.

**Do NOT build (MVP — explicitly deferred):**
- Separate "sleep-time primary+secondary agent" architecture (Letta's two-agent model).
  Alfred's dream is a single background pass, not a co-resident shadow agent sharing live
  memory blocks. (The frozen-prefix discipline means there's no live block to co-edit
  anyway.)
- Embedding/ANN-backed dedup, learned reranker, or fitness-weighted retention model — no
  traces to tune on yet; pure threshold + arithmetic suffices (CEO warning, mirrors
  store-memory.md's ranker stance).
- Global clustering (k-means over the whole corpus). MVP uses per-fact neighbor buckets.
  Promote to global clustering only if buckets miss cross-bucket duplicates at scale.
- Hard delete / purge. MVP archives only. Purge of `.archive/` is a separate later op.
- Contradiction *detection* as a standalone NLP feature — MVP gets contradiction signal
  for free as a byproduct of the dedup/merge LLM judge ("these conflict; newer wins"),
  not a dedicated pass.
- Spaced-repetition / forgetting-curve scheduling sophistication beyond the single
  exponential-recency term.

The MVP is ~one event subscriber + four small functions over the memory store's existing
primitives. It is intentionally boring. Its value scales with corpus cruft, so shipping it
dumb-but-correct and measuring (via the eval harness, module #17: corpus size / retrieval
precision before vs after dream) is the right sequencing.

---

## Industry refs (with URLs)

- **Letta — sleep-time compute / sleeptime agents** (the trigger model): background agent
  runs during the primary agent's idle periods to consolidate fragmented memories,
  deduplicate, reorganize, and prune outdated info — shift compute off the latency path.
  Alfred's dream is the *single-pass, frozen-prefix* simplification of this.
  https://www.letta.com/blog/sleep-time-compute
  https://docs.letta.com/guides/agents/architectures/sleeptime/
  https://forum.letta.com/t/sleeptime-agents-for-memory-consolidation-best-practices-guide/154
- **Letta — context repositories / defragmentation**: background subagent splits/merges
  memory files into a clean 15–25-file hierarchy — the exact merge/dedup shape, memory-only.
  https://www.letta.com/blog/context-repositories/
- **mem0 — consolidation + ADD/UPDATE/DELETE/NOOP**: periodic scan; vector similarity
  plus LLM conflict resolution in their stack. Alfred reuses the batch NOOP/merge shape,
  but keeps embeddings out of the MVP default.
  https://mem0.ai/blog/state-of-ai-agent-memory-2026
  https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents
  https://arxiv.org/html/2504.19413v1
- **Stanford Generative Agents (reflection + recency×importance×relevance)**: the
  three-factor score (exp-decay recency 0.995, importance, relevance) Alfred repurposes as
  a *retention* score; reflection = periodic synthesis of recent memories into higher-level
  entries = dream's merge step.
  https://ar5iv.labs.arxiv.org/html/2304.03442
  https://dl.acm.org/doi/10.1145/3586183.3606763
- **Forgetting / decay (2026)**: Ebbinghaus exponential decay + access-frequency
  reinforcement + active supersession; "TTL on long-tail + LRU-style score decay + active
  supersession on every write" as the production policy dream implements (archive, not
  delete).
  https://mem0.ai/blog/memory-eviction-and-forgetting-in-ai-agents
  https://arxiv.org/html/2604.20300  (FSFM — biologically-inspired selective forgetting)
  https://dev.to/sudarshangouda/ai-agent-memory-part-2-the-case-for-intelligent-forgetting-4i48
  https://tianpan.co/blog/2026-04-12-the-forgetting-problem-when-agent-memory-becomes-a-liability
- **Semantic deduplication (SemDeDup) — cluster/ANN → pairwise cosine → threshold**: the
  candidate-selection + dedup algorithm shape (don't do global O(N²); ANN-bucket then
  threshold).
  https://docs.nvidia.com/nemo-framework/user-guide/latest/datacuration/semdedup.html
  https://encord.com/blog/complete-guide-to-embeddings-in-2026/

---

## Open questions

1. **`access_count` / `last_retrieved` reciprocal write.** Decay's frequency+recency terms
   need the store to bump these fields whenever `prefetch`/`memory_search` surfaces a fact.
   store-memory.md exposes `last_retrieved` as a read field (its open-Q #5) but doesn't yet
   say *who writes the hit-count*. Proposal: the store bumps it on retrieval (cheap, it's
   the retriever); dream only reads it. Confirm with store-memory owner.
2. **Decay default aggressiveness.** Conservative-by-default is the call (rarely fire), but
   what concrete `forget_threshold` / `ttl_floor` / weights? Defer to the eval harness
   (module #17): measure retrieval precision/recall before vs after, tune so dream never
   archives a fact that later gets searched for. Ship with decay effectively near-off until
   measured.
3. **`assist`-mode diff UX.** In `assist`, dream proposes a consolidation diff for
   confirmation. What's the surface — a CLI prompt? a queued proposal the daemon holds? The
   distill new-skill gate (#20c) faces the identical "propose → confirm → apply" problem;
   dream should reuse whatever confirmation mechanism distill builds (shared gate UX, not a
   bespoke one).
4. **Pass scope: session_end (narrow) vs idle (broad).** Two triggers, two scopes. Is the
   session_end scoped pass worth it, or should dream only run on `idle` to batch more? MVP:
   keep both but make session_end cheap (only that session's dirty facts); revisit if it
   causes churn (merging facts the next session would've added context to). Possibly gate
   session_end behind "≥N new facts this session".
5. **Future semantic adapter consistency.** If a later memory provider adds embeddings,
   dream must use that provider's own semantic primitive and recompute adapter-owned
   derived data after merges. The default files provider exposes only FTS/entity/recency
   primitives.
6. **Multi-process write contention.** Daemon dream batch-merge + interactive CLI agent
   `memory_append` may collide on `facts/` + index. Reuse session/skill-store discipline:
   SQLite WAL `BEGIN IMMEDIATE` for index, atomic-rename + per-fact lock for files
   (store-memory open-Q #6, skill-store M4). dream's batch is the larger transaction —
   confirm it doesn't starve interactive writes (keep the txn short: compute diff outside
   the lock, apply inside).
7. **Interaction with `dream.consolidated` → distill.** If distill listens and re-mines
   traces after dream tidies memory, is there any ordering hazard? Per Decision #18 they're
   disjoint (memory vs skills) so no shared state — but worth a one-line confirmation that
   distill listening is purely advisory (e.g. "memory changed, maybe re-evaluate") and
   creates no dream→distill→dream cycle.
