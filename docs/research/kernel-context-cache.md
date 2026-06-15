# Ring-1 Kernel — Context Assembly + Prompt-Cache Discipline

Module research for Alfred design spec. Refines Decisions #21 / #29 and resolves Eng-review **H3**.
Date: 2026-06-15. All field names verified against official Anthropic + LiteLLM docs (see Industry refs).

---

## Module scope

The Ring-1 context-assembly component owns three jobs, all of them deploy-coupled to cache economics:

1. **Frozen-prefix assembly** — build the static system prompt once at `session_start`
   (`persona → user → project_instructions → memory(facts) → skill_l0`), place one
   cache breakpoint at its end, never mutate it
   mid-session.
2. **Per-turn message assembly** — append the rolling conversation tail after the frozen prefix; maintain a
   small rolling set of cache breakpoints on recent turns.
3. **Compression** — when input tokens cross a threshold, summarize the *middle* of the conversation while
   keeping the frozen prefix (and its cache) byte-identical.

Out of scope (other modules): provider wire-format conversion (`LiteLLMProvider`, provider-layer module),
memory retrieval itself (store:memory), skill scanning (store:skill-loader), iteration budget (kernel:loop).
This module *consumes* their outputs at `session_start` and freezes them.

The governing constraint: **cache read is ~10% of input price; a silent cache miss is a ~10× cost regression
that produces correct output** — exactly the Fail-Loud target. The discipline is therefore a Ring-1 invariant,
not a tunable.

---

## Recommended design

### 1. Prompt assembly order and the cache breakpoint

Cache validity follows the hierarchy **`tools` → `system` → `messages`**; a change at any level invalidates that
level *and everything after it* (Anthropic docs). Provider tool schemas are passed through
the provider's tool field and must also be stable, but the **textual frozen system prefix**
uses the layered-instructions order L6. The rule is **most-stable-first**, and the single
static breakpoint goes at the end of the last stable text block.

Recommended frozen-prefix order (all assembled once, then frozen):

| # | Block | Stability | Why this position |
|---|-------|-----------|-------------------|
| provider | **tools** (local + mcp tool schemas) | highest — set at session_start | tool change invalidates *everything* in provider cache terms; keep schemas stable and sorted, but not as text in the system prefix |
| 1 | **persona / soul** (identity, operating rules) | high — global memory core | rarely changes within a process lifetime |
| 2 | **user profile** (global `core/user.md`) | high — global memory core | stable user facts before project-specific rules |
| 3 | **project instructions** (`AGENTS.md` merged) | per-tree, frozen at session_start | project rules apply before retrieved facts and skills |
| 4 | **retrieved memory facts** (top-k from store:memory, NOT a full dump) | frozen at session_start | retrieval result is per-session but fixed once chosen (Decision #17a) |
| 5 | **skill L0 index** (name+description for every loaded skill) | frozen at session_start | snapshot per Decision #12; a few KB even for dozens of skills |
| optional | **goal injection** (if set, Decision #19) | frozen at session_start | session-control block; must be deterministic and cache-stable |
| **⎯** | **← CACHE BREAKPOINT (`cache_control: ephemeral`)** | | end of static prefix |
| 6 | conversation tail (user/assistant/tool messages) | volatile | grows every turn; never part of the frozen prefix |

This matches Hermes (system prompt = single stable cached block; MEMORY.md/USER.md frozen-snapshot at session
init) and Anthropic's own guidance ("add a `cache_control` breakpoint at the **end of your system prompt**").
Claude Code confirms tool definitions are the largest single component (14–17K tok vs ~2.5K system) — putting
them first and frozen is where most of the cache savings live.

**Vendor injection at the provider boundary (per Decision #29):**
- **Anthropic**: the boundary layer attaches `"cache_control": {"type": "ephemeral"}` to the final content block
  of the system array (LiteLLM passthrough). Optionally `"ttl": "1h"` for daemons with >5-min idle gaps.
- **OpenAI/Azure**: no annotation — automatic prefix caching for any ≥1024-token prefix. The discipline is
  identical (prefix byte-stability); only the annotation differs. The boundary layer is the *only* place that
  knows the vendor.

**Minimum-length reality check (verified):** caching only engages above a per-model floor. Claude **Opus 4.8 =
1,024 tokens**, Sonnet 4.5/4.6 = 1,024, Opus 4.5/4.6 = 4,096, Haiku 4.5 = 4,096. OpenAI = 1,024. With tools
(14–17K) + persona + skills L0, Alfred's prefix clears every floor comfortably — but the runtime warning (§4)
must distinguish "stayed 0 because below floor" from "stayed 0 because prefix mutated."

### 2. Rolling tail breakpoints (cheap, optional, recommended)

Anthropic allows **up to 4 cache breakpoints**. Use breakpoint #1 for the frozen prefix; spend the remaining
budget on a **rolling window** of the last 1–3 turns (Hermes uses system + 3 most-recent messages = 4 total).
This caches the bulk of a long conversation, not just the prefix, and re-establishes within 1–2 turns after any
disruption. MVP may ship prefix-only (1 breakpoint) and add the rolling window as a measured optimization;
mark it explicitly so the warning logic knows how many breakpoints to expect.

### 3. Frozen-prefix is immutable for the session's life

No hot-reload of skills or memory mid-session (Decision #12). Edits land **next session**. The hard part —
what "next session" means in a daemon — is **§ Gap answers (H3)**.

---

## Interface sketch

```python
# kernel/context.py  — Ring-1, vendor-agnostic. Produces Alfred's own message/prompt types.

class FrozenPrefix(BaseModel):
    """Assembled once at session_start, then immutable for the session's life."""
    tools: list[ToolSchema]          # provider tool field; sorted/stable, not textual prefix
    persona: str                     # text block 1
    user: str                        # text block 2
    project_instructions: str        # text block 3
    memory: list[MemoryChunk]        # text block 4 — retrieved top-k, NOT a dump
    skill_l0: list[SkillCard]        # text block 5 — name+description, snapshot
    goal: str | None                 # optional deterministic session-control block
    fingerprint: str                 # sha256 of the serialized prefix — drift detector

    model_config = ConfigDict(frozen=True)   # mutation raises -> Fail-Loud

class ContextAssembler:
    def __init__(self, prefix: FrozenPrefix, *, rolling_breakpoints: int = 0): ...

    def assemble(self, tail: list[Message]) -> AssembledPrompt:
        """frozen prefix + conversation tail. Breakpoint marker at end of prefix
        (+ optional rolling markers on last N tail msgs). Vendor-neutral —
        the provider boundary translates the markers."""

    def compress(self, tail: list[Message]) -> list[Message]:
        """summarize-middle; prefix untouched (see Compression)."""

# provider boundary (LiteLLMProvider) — the ONLY vendor-aware code:
#   anthropic -> set cache_control:{type:ephemeral[,ttl]} on the marked block
#   openai    -> no-op (auto prefix cache); rely on byte-stable prefix

# verification — read normalized usage after every call:
class CacheUsage(BaseModel):
    cached_tokens: int        # LiteLLM:  usage.prompt_tokens_details.cached_tokens  (both vendors)
    cache_read: int           # Anthropic native: usage.cache_read_input_tokens
    cache_write: int          # Anthropic native: usage.cache_creation_input_tokens
    prompt_tokens: int        # NOTE: includes BOTH cached + uncached input tokens
```

A session-start assertion recomputes `fingerprint` from the live prefix on every `assemble()` in debug mode; a
changed fingerprint within one session is a kernel bug and raises (the prefix must be frozen).

---

## Gap answers (H3) — frozen-prefix vs mid-session writes in a long-lived daemon

**The problem (restated).** Spec says skill/memory edits "take effect next session." For a cron tick that's
fine. For a daemon held open for *days*, "the session" never ends, so distill/evolve/dream outputs produced at
`idle`/`tick` **never load** — the agent improves on disk but not in its own running context. This is real.

**Root cause.** Two different things are both called "session": (a) the *cache identity* (a byte-stable frozen
prefix), and (b) the *agent lifetime* (the process holding the asyncio loop, Decision #6). Conflating them is
the bug. The fix is to **decouple them**: the agent lifetime can span many *prefix epochs*.

### Definition: a "session" = one frozen-prefix epoch, not one process lifetime

A session is the span over which the frozen prefix (and therefore the cache identity) is held constant. A
process may live through N sessions. Re-assembling the prefix opens a new session/epoch. This is the SSoT for
the word "session" across the spec.

### (a) Cron ticks — already correct, no change

Hermes model (verified): **each cron tick spawns a fresh agent session with no history; attached skills load
fresh; prompt runs; output delivered; state updates.** A fresh session = a fresh prefix assembly = newest
skills/memory loaded **for free** — the cache write happens once per fresh session regardless. There is **no
cache penalty** to picking up new skills on a cron tick, because a fresh session was always going to pay one
cache-creation write. **Recommendation: cron tick = fresh session per tick (adopt Hermes verbatim).** Distill
at tick T writes a skill; the tick T+1 fresh session loads it. Latency of adoption = one tick. Acceptable.

### (b) Interactive / long-lived daemon sessions — needs an explicit re-freeze trigger

A continuous interactive session cannot silently re-read skills mid-turn (that breaks the cache and the
frozen-prefix invariant). It also cannot ignore new skills forever. Resolution: an **explicit, bounded
re-freeze** at a turn boundary.

**Mechanism — "epoch roll" at `turn_end`:**

1. Subsystems (distill/evolve/dream) that mutate skills/memory **emit an event** after a confirmed write
   (`skill.changed` / `memory.changed`) — push, not poll. These are Ring-3 plugin events; the kernel already
   has the bus.
2. The kernel sets a `prefix_dirty` flag (one bool; it does **not** reload immediately — that would corrupt
   the in-flight turn's cache).
3. At the next **`turn_end`** (a natural boundary, no turn in flight), if `prefix_dirty`:
   - re-run `session_start` assembly → new `FrozenPrefix` with a new `fingerprint`,
   - the conversation **tail is preserved** (history continues; only the prefix epoch rolls),
   - emit `session_start` again (epoch N+1) so goal/memory injection re-fire,
   - clear the flag.
4. **Cache cost of a roll:** the new prefix pays exactly one cache-creation write on the next call, then reads
   resume. Cost = one cache-miss turn per epoch roll, not per turn. With confirm-gated distill/evolve
   (Decision #20c), rolls are rare (operator-confirmed), so the amortized cost is negligible.

**Governance:** the re-freeze is gated by `autonomy` (Decision #20c). `off` → never auto-roll (operator rolls
manually, e.g. `/reload`). `assist` → roll only after the human confirms the underlying distill/evolve write
(the same confirm that gates the write). `auto` → roll automatically at the next `turn_end`. This reuses the
existing e-stop; no new control surface.

**Why `turn_end`, not `idle` or mid-turn:** mid-turn would break the in-flight cache and violate the frozen
invariant; `idle` could roll the prefix while the user is mid-thought, surprising them on their next message.
`turn_end` is the same push-don't-poll boundary `goal` and `dream` already use.

**`alfred-agent` config self-edit (Decision #24) stays restart-only.** Config changes (model, providers,
autonomy) alter more than the prefix — they alter wiring — so those remain restart-to-apply. Only **skill/memory**
deltas qualify for an in-process epoch roll. This is a deliberate narrower scope than "reload everything."

**Summary table:**

| Session type | Boundary = | New skills/memory load when | Cache cost |
|---|---|---|---|
| cron tick | every tick (fresh session) | next tick | 1 write/tick (unavoidable anyway) |
| interactive daemon | epoch roll at `turn_end` when `prefix_dirty` + autonomy allows | next `turn_end` after a confirmed write | 1 write per roll (rare) |
| config edit (`alfred-agent`) | process restart | restart | full re-warm |

This closes H3: a daemon picks up its own self-improvement within one tick (cron) or one turn (interactive),
without ever mutating a prefix mid-turn and without paying per-turn cache penalties.

---

## Compression — when, how, keeping the cache prefix stable

**Trigger.** Watch `prompt_tokens` (which includes cached tokens — verified) per call. Fire compression when it
crosses a threshold. Two reference points:
- **Hermes**: `threshold × context_length`, default **0.50** (compress at 50% of context).
- **Anthropic native compaction (`compact_20260112` beta)**: default trigger **150,000 input tokens** (min
  50,000), returns a `compaction` content block that must be passed back; the API then auto-drops messages
  before it.

**Recommendation: summarize-middle, never truncate; protect head and tail.**

- **Head (always protected):** the frozen prefix — *never touched*. This is the whole point: compression must
  leave the cached prefix byte-identical so its cache survives (Anthropic + Hermes both confirm "system prompt
  cache survives compression").
- **Tail (always protected):** the last N recent messages — Hermes protects `protect_last_n = 20` messages, or
  a token budget of `~0.20 × threshold`, whichever preserves more. Recent context is where the model is working.
- **Middle (compressed):** turns between head and tail are sent to an auxiliary LLM (call via an injected
  provider, never the loop) with a **structured** summary template — Goal / Constraints / Progress
  (Done·InProgress·Blocked) / Key Decisions / Relevant Files / Next Steps / Critical Context. A cheap pre-pass
  first replaces verbose tool results (>~200 chars, outside the tail) with stubs — often enough on its own.

**Why summarize-middle beats truncate:** truncation silently drops the middle (loses decisions/constraints) —
that's the silent-degradation anti-pattern. Summarize-middle keeps the *information* while shedding tokens, and
the structured template makes the loss legible.

**Cache interaction (the critical bit):**
- The frozen prefix cache is **untouched** (head protected) → survives every compression. This is why the
  breakpoint goes at the *end of the static prefix* and the prefix excludes all conversation.
- The compressed-middle region's cache is invalidated (its content changed) — unavoidable and correct.
- The **rolling tail breakpoints re-establish within 1–2 turns** (Hermes), so the post-compression cache-cold
  window is short.
- If using Anthropic native compaction, put a `cache_control` breakpoint on the `compaction` block too, so the
  summary itself caches on the turn after it's created.

**Build choice for MVP:** implement summarize-middle in-kernel (vendor-neutral, works for both Anthropic and
OpenAI, full control over the template and tail protection). Anthropic's native `compact_20260112` is a
fallback to evaluate later — it's Anthropic-only and beta-gated, so it can't be the cross-vendor default
(Decision #26 requires both vendors). Keep the in-kernel summarizer as SSoT; native compaction is an A/B
alternative behind the provider boundary.

---

## Verifying cache works — exact fields + runtime warning

### Field names (VERIFIED against official docs — do not substitute)

**Via LiteLLM (Alfred's default path, both vendors normalized):**
- `usage.prompt_tokens_details.cached_tokens` — cache-hit tokens for the call (OpenAI-shaped, populated for
  **both** vendors by LiteLLM). **This is the field e2e #17 already names — correct.**
- LiteLLM **also** surfaces Anthropic's native `usage.cache_creation_input_tokens` and
  `usage.cache_read_input_tokens` when the call went to Anthropic.
- ⚠ `usage.prompt_tokens` includes **both** cached and uncached input tokens (LiteLLM bug-report-confirmed
  semantics). Do **not** infer cache state from `prompt_tokens` alone — read `cached_tokens` directly.

**Native Anthropic (if a future provider bypasses LiteLLM):**
- `usage.cache_creation_input_tokens` — tokens **written** to cache (charged at write rate).
- `usage.cache_read_input_tokens` — tokens **read** from cache (~10% input price). **>0 = cache hit.**
- `usage.input_tokens` — tokens **after** the last breakpoint (uncached). Total input =
  `cache_read + cache_creation + input_tokens`.
- 1-hour vs 5-min split appears under `usage.cache_creation.ephemeral_5m_input_tokens` /
  `ephemeral_1h_input_tokens`.

**Native OpenAI:** `usage.prompt_tokens_details.cached_tokens`. Automatic; no annotation.

**Expected pattern (the assertion for e2e #17):**
- **Turn 1** (cache write): Anthropic `cache_creation_input_tokens > 0`, `cache_read_input_tokens ≈ 0`;
  normalized `cached_tokens ≈ 0`.
- **Turn 2+** (cache hit): `cache_read_input_tokens > 0` (Anthropic) / `cached_tokens > 0` (normalized),
  ≈ the frozen-prefix token count.

### Runtime warning (Fail-Loud, Decision #29c)

After each provider call, the boundary layer reads normalized `cached_tokens` and tracks it across the session.
The warning must avoid two false positives:

```
WARN trigger: a multi-turn (--continue / ≥2-turn) session where cached_tokens stays 0 on turn ≥2
              AND frozen-prefix token count ≥ the model's minimum-cacheable floor.

Suppress (NOT a fault):
  - turn 1 (write turn — read is expected 0)
  - frozen prefix below the model floor (e.g. <1024 for Opus 4.8 / Sonnet / OpenAI) -> log DEBUG "below cache floor", not WARN
  - the turn immediately after an epoch roll (H3) or a compression event (expected one cold turn)
```

Message (per Anthropic's own guidance — "if `cache_read_input_tokens` is consistently zero across a multi-turn
conversation, caching isn't happening"):

```
WARNING cache_read stayed 0 across N turns (prefix=<tok> ≥ floor=<floor>). Prompt cache is NOT engaging —
        likely the frozen prefix is being mutated mid-session. Expect ~10× input cost. Check context assembly.
```

The most common real cause of a stuck-zero in *this* architecture is an accidental mid-session prefix mutation
(non-determinism in tool-schema ordering, a timestamp in persona, retrieval re-ordering) — exactly what the
`fingerprint` drift-detector in the interface sketch catches earlier. Ship both: fingerprint assert (catches the
bug at assembly) + cached_tokens warning (catches it at runtime, including provider-side surprises).

---

## Industry refs (URLs)

- Anthropic — Prompt caching (cache_control ephemeral, `cache_read_input_tokens` / `cache_creation_input_tokens`,
  per-model minimums, 4-breakpoint max, invalidation hierarchy, "consistently zero = not caching"):
  https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Anthropic — Compaction (`compact_20260112`, `trigger` default 150k / min 50k, `compaction` block,
  `pause_after_compaction`, system-prompt cache survival, breakpoint-on-compaction-block):
  https://platform.claude.com/docs/en/build-with-claude/compaction
- LiteLLM — Prompt Caching (`prompt_tokens_details.cached_tokens` normalized both vendors; surfaces
  `cache_creation_input_tokens`/`cache_read_input_tokens`; OpenAI auto ≥1024):
  https://docs.litellm.ai/docs/completion/prompt_caching
- LiteLLM — `prompt_tokens` includes cached tokens (semantics confirmation):
  https://github.com/BerriAI/litellm/issues/15945
- Hermes Agent — Context Compression & Caching (0.50 threshold, `protect_last_n=20`, head-middle-tail,
  4 breakpoints = system + 3 rolling, system-prompt cache survives compression, structured summary template):
  https://hermes-agent.nousresearch.com/docs/developer-guide/context-compression-and-caching
- Hermes Agent — 5 pillars + frozen-snapshot system prompt (MEMORY.md/USER.md frozen at session init,
  cache-aware): https://www.mindstudio.ai/blog/hermes-agent-five-pillars-memory-skills-soul-crons
- Hermes — cron = fresh session per tick, skills injected per tick:
  https://hermes-tutorials.dev/blog/cron-job-patterns-2026/
- Claude Code — system-prompt assembly order + tool defs are the largest block (14–17K vs ~2.5K system):
  https://www.dbreunig.com/2026/04/04/how-claude-code-builds-a-system-prompt.html
- Claude Code — context compaction pipeline (95% trigger, progressive compression):
  https://harrisonsec.com/blog/claude-code-context-engineering-compression-pipeline/

---

## Open questions

1. **Rolling tail breakpoints in MVP or v2?** Prefix-only (1 breakpoint) is the simplest correct thing; the
   rolling 3-message window (Hermes) is a measured cost optimization. Recommend MVP = prefix-only, add rolling
   window once the eval harness (module #17) can show the $ delta. Decide whether e2e #17 should also assert
   tail-cache hits or only prefix-cache hits.
2. **Epoch-roll observability:** should an epoch roll emit a distinct `session.reframed` event (vs re-emitting
   `session_start`)? A distinct event is cleaner for the SSE/TUI consumer but adds to the kernel event catalog
   (Decision #7's "no speculative events" rule). Leaning: re-emit `session_start` with an `epoch: int` field.
3. **Compression auxiliary-LLM provider:** which provider summarizes the middle — the session's own provider,
   or a cheaper dedicated one (e.g. Haiku) injected via config? Cheaper is better for cost but adds a config
   knob. Defer to provider-layer module.
4. **OpenAI cache verification weakness:** OpenAI auto-caching gives no write signal (only `cached_tokens` on
   read) and no guaranteed minimum behavior — the "stayed 0" warning is the only signal. Confirm during e2e #17
   that OpenAI/Azure-via-proxy actually returns `prompt_tokens_details.cached_tokens` through the proxy gateway
   (some proxies strip it — see LiteLLM issue #6229 / #18219).
5. **Native compaction A/B:** worth wiring `compact_20260112` behind the boundary as an Anthropic-only A/B arm
   against the in-kernel summarizer, to measure summary quality vs cost? Defer until in-kernel summarizer ships.
