# Subsystem Research: goal (Ring-3, S6)

Date: 2026-06-15
Module: Ring-3 subsystem — goal (persistent objectives, Codex `/goal` model)
Decision basis: Decision #19 (Codex `/goal`), #20c (autonomy), #17 (trace store off-track), #21/#29 (frozen prefix), Eng review M5 (no-progress detector).

---

## Module scope

`goal` is the S6 Ring-3 subsystem. It owns **persistent objective state** that survives session
end / interruption / token-limit, and it **self-drives the loop** when an objective is unmet. Per
the ring invariant it is purely an event subscriber + registry entries; it never injects logic into
the kernel loop and never calls a model itself (Decision #19: "calls model: no (drives)"). Its only
effect on the loop is to **inject a synthetic user turn** ("continue") via the normal input path.

Two hooks, exactly as the spec table says:
- `session_start` → inject the active goal into the frozen prefix.
- `turn_end` → if goal unmet & not paused & gates pass → enqueue a self-continuation turn.

Out of scope for this subsystem (lives elsewhere, goal only consumes/coordinates):
- The actual "is the agent looping" signal source = **trace store off-track markers** (Decision #17).
  goal reads them; it does not compute trajectory annotations itself.
- The global e-stop = **autonomy gate** (#20c). goal asks the gate; it does not own it.

Verbs (Decision #19, matches Codex exactly): `set` (create/replace) / `view` / `pause` / `resume`
/ `clear`. Exposed as the bundled `set-goal` skill (Decision #24) which calls goal tools registered
into the `tools` registry.

---

## Goal state machine

### Source of truth: the real Codex schema

Inspected `~/.codex/goals_1.sqlite` (structure only, no objective text read). Single domain table,
SQLx-migrated, WAL mode. This is the model to adopt:

```sql
CREATE TABLE thread_goals (
    thread_id          TEXT PRIMARY KEY NOT NULL,   -- one active goal per thread/session-lineage
    goal_id            TEXT NOT NULL,               -- stable id across set/replace within a thread
    objective          TEXT NOT NULL,               -- the mission text
    status             TEXT NOT NULL CHECK(status IN (
                          'active','paused','blocked',
                          'usage_limited','budget_limited','complete')),
    token_budget       INTEGER,                     -- NULL = no cap (default; "high/off" per #19)
    tokens_used        INTEGER NOT NULL DEFAULT 0,
    time_used_seconds  INTEGER NOT NULL DEFAULT 0,
    created_at_ms      INTEGER NOT NULL,
    updated_at_ms      INTEGER NOT NULL
);
```

Key findings from the live DB:
- **Keyed by `thread_id`, not session_id** — the goal binds to a conversation *lineage*, which is
  exactly how it survives `--continue` / resume. A new session that continues a thread re-reads the
  same row. This is the cross-session survival mechanism (see below).
- Codex has **6 statuses**, not the 5 verbs imply. The extra three (`blocked`, `usage_limited`,
  `budget_limited`) are *terminal-ish suspension* states distinct from a user `pause`. Critical
  insight for M5: Codex already separates "user paused me" from "I was stopped by a resource/usage
  limit." Alfred should mirror this and **add a 7th for no-progress** (see Gap answers).
- `token_budget` was **NULL** in the live row → confirms Decision #19's "default high/off". Budget is
  opt-in, not the primary guard.
- Resource counters (`tokens_used`, `time_used_seconds`) live **with** the goal, not derived from the
  loop ledger — so the budget check is a pure read on this row, no cross-subsystem coupling at
  check time.
- Persistence path in Codex source: `codex-rs/state/src/runtime/goals.rs`, "persisted per-thread in
  the thread store." Config gate: `~/.codex/config.toml` → `[features] goals = true` (a bare feature
  flag; **no inline goal config block** — all goal state is in the sqlite, none in config.toml).

### Alfred state machine (recommended)

Adopt Codex's table 1:1, add `no_progress` to the status enum, store in a dedicated Ring-2-adjacent
sqlite (`goals.db`, WAL) so it is independent of the session store (SSoT: goal state ≠ conversation
record, same separation principle as trace vs session in Decision #17).

States and the single field that encodes each (no duplicate "is_paused" bools — Wayne SSoT rule):

| status | meaning | self-continues at turn_end? | set by |
|---|---|---|---|
| `active` | pursuing, unmet | yes (if gates pass) | `set`, `resume` |
| `paused` | user-suspended | no | `pause` verb |
| `complete` | objective met | no | `set_goal(complete)` self-report OR explicit |
| `blocked` | agent declared it cannot proceed (needs human/external) | no | `set_goal(block, reason)` |
| `budget_limited` | token/time/turn cap hit | no (until budget raised + resume) | budget guard |
| `no_progress` | **NEW** — repeated-state detector tripped | no (until resume) | progress detector (M5) |

Transition rules (the whole machine — deliberately small):

```
            set
   (none) ───────► active
                     │  ▲
       pause         │  │ resume
   active ──────► paused ──┘
                     │
   resume from any suspended state ─► active
                     │
   turn_end & unmet & active & gates_ok ─► active (self-continue, no state change)
                     │
   set_goal(complete) | objective verified ─► complete
   set_goal(block,reason)                  ─► blocked
   budget guard trips                      ─► budget_limited
   progress detector trips (M5)            ─► no_progress
   clear                                   ─► (row deleted)
```

Invariants:
- Exactly **one active goal per thread** (PRIMARY KEY thread_id). `set` on an existing goal =
  replace (new `goal_id`, reset counters) — matches Codex "create or replace".
- All suspended states (`paused`/`blocked`/`budget_limited`/`no_progress`) are **non-driving** and
  require explicit `resume` (or budget raise) to return to `active`. Only `paused` is user-intent;
  the other three are fail-loud system suspensions that **emit an event** (`goal.suspended` with the
  reason) so the UI/CLI surfaces *why* the loop stopped (Fail-Loud: never silently stop self-driving).

### Cross-session survival

1. `set` writes the row keyed by the current `thread_id`.
2. Session ends / process dies / token limit hit → row persists (WAL, fsync on write).
3. New session that continues the thread (`alfred chat --continue`) re-derives the same `thread_id`
   and the `session_start` subscriber reads the row back.
4. If status is `active`, the goal is re-injected into the new session's frozen prefix AND the loop
   self-continues from the first turn_end — i.e. interruption is transparent.
5. `tokens_used` / `time_used_seconds` accumulate **across** sessions of the same thread (the budget
   is a lifetime-of-goal bound, not per-session) — matches the counters living on the goal row.

---

## Self-continuation

### How "is goal met?" is judged

Codex's answer (verified): the model self-reports via a **`set_goal` tool** — "update goal state —
mark complete, update progress, refine objective." There is **no code-side completion oracle**; the
agent decides it is done and calls the tool. Alfred adopts the same primitive:

- Register a `set_goal` tool (handler in the goal subsystem) with actions:
  `complete` | `update_progress(note)` | `refine(objective)` | `block(reason)`.
- "Goal met" = the model called `set_goal(complete)` in the just-finished turn → status → `complete`.
- This is **model-judged, not regex-judged** — correct, because completion of an open-ended
  objective is not code-decidable (the Ralph "completion marker file" pattern is the file-based
  equivalent; `set_goal(complete)` is the structured-tool equivalent and is cleaner for an SDK).
- **Anti-gaming note (ties to M5):** because the model can lie/hallucinate "done", `set_goal(complete)`
  is necessary but the progress detector is the independent cross-check. A `complete` claim is
  accepted, but a goal that oscillates `update_progress` → no real change is caught by the detector.

### How "continue" is injected, and loop interaction

The kernel loop (Ring 1) is unaware of goal — it only knows `ModelProvider.complete()` and the input
path. goal self-continues by **feeding a synthetic turn through the same input path the user uses** —
not by mutating the loop. Mechanism:

1. Loop finishes a turn → emits `turn_end` (async dispatch, per Decision #7).
2. goal's `turn_end` subscriber runs the **continuation decision** (below).
3. If decision = continue: subscriber enqueues a synthetic user message `"continue"` (or a richer
   "the goal is not yet met; continue toward: <objective>") onto the session's input queue.
4. The loop picks it up as the next turn's input — identical code path to a real user message. The
   loop never knows it was self-generated (SSoT: one input path).

This is **push, not poll** (Wayne rule, Decision #19 explicit): there is **no `turn_start` poll**
asking "should I keep going?". The driver is the `turn_end` edge. A turn ending is the event; the
continuation is the reaction.

Codex parallel: "The app-server can re-trigger the agent when a goal is active — enabling autonomous
continuation without user input." Alfred's in-process equivalent is the synthetic-input enqueue;
the `agentkit-server` headless daemon equivalent is literally re-triggering the loop over SSE.

### The continuation decision (turn_end subscriber logic)

Evaluated in this order — **first failing gate stops self-continuation and sets the matching status**:

```
on turn_end:
  goal = load(thread_id)
  if goal is None or goal.status != 'active':        return            # nothing to drive
  if model_called set_goal(complete) this turn:       set complete;  return
  if model_called set_goal(block):                    set blocked;   return
  if autonomy == 'off':                               return            # e-stop, stay active, no drive
  if autonomy == 'assist':                            request_confirmation(); return-or-continue
  # ---- progress bound (M5) — BEFORE budget, independent of it ----
  if progress_detector.is_stuck(thread_id):           set no_progress; emit goal.suspended; return
  if self_continuations >= max_self_continuations:    set no_progress; emit goal.suspended; return
  # ---- resource bound (safety net) ----
  if goal.token_budget and goal.tokens_used >= goal.token_budget:
                                                      set budget_limited; emit goal.suspended; return
  # ---- all gates pass ----
  inject_input("continue toward: " + goal.objective)
  goal.self_continuations += 1
```

---

## Gap answers (M5 no-progress)

**The gap (Eng review M5):** self-continuation guarded only by budget is a *resource* bound, not a
*progress* bound. "continue → no progress → continue" burns budget producing nothing, worst under
`autonomy=auto`. Budget (default high/off) is the wrong and only guard.

**Answer: two independent progress bounds, both checked BEFORE the budget gate, both budget-blind.**

### 1. Repeated-state / no-progress detector

Adopt the Hermes-agent SHA-256 tool-call loop guard (issue #481) — directly applicable since Alfred
is Hermes-lineage. Concrete, verified parameters:

- **Fingerprint** = `SHA256(tool_name + serialized_args)` per tool call. **Input-only** — tool
  *output* is NOT hashed (Hermes design). Rationale: identical call = no new information sought.
  - *Alfred refinement:* also hash a **turn-level fingerprint** = `SHA256(sorted(tool fingerprints
    this turn) + assistant_text_normalized)`. The Hermes guard is per-tool-call; goal drives at
    *turn* granularity, so we need a turn-level repeat signal too. A turn that produces the same set
    of tool calls + same message as a prior turn = no progress even if no single tool repeats 3×.
- **Sliding window** = last 10 turns (Hermes uses window 10 for calls; reuse for turns).
- **Patterns detected** (Hermes):
  - exact repeat `A-A-A`: same turn fingerprint **3** times in a row → stuck.
  - ping-pong `A-B-A-B`: alternating 2 fingerprints across the window → stuck.
  - (cycle `A-B-C-A-B-C`: Phase-2, defer — YAGNI for MVP.)
- **Escalation** (Hermes warn-then-block, `max_warnings_before_block: 2`): on first/second detection,
  inject a system note into the continuation message ("you appear to be repeating; change approach")
  — this gives the model a chance to break out. On the 3rd detection (warnings exhausted) → set
  `no_progress`, suspend, emit `goal.suspended(reason=no_progress)`. This warn-first design is why
  the detector is *better* than a hard counter: it self-heals the common transient loop before
  killing the goal.
- **Exemptions:** legitimately-repetitive tools (long-poll/`process`/sleep-wait) are tagged
  `loop_exempt=true` in the tools registry and excluded from the fingerprint (Hermes exemption list),
  preventing false positives on valid polling.

### 2. `max_self_continuations` (hard counter, budget-independent)

A per-goal integer cap on **consecutive self-driven turns** (reset to 0 by any real user message).
This is the Ralph-loop `MAX_ITER` discipline (Ralph default 10) and the 2026 circuit-breaker
consensus ("max steps... when any cap is hit, the agent must STOP or ESCALATE — never continue").

- **Default = 25** (generous — long-horizon is the point — but finite; Ralph's 10 is for short PRD
  loops, goal mode is longer-horizon so a higher default fits). Configurable in `AgentConfig`.
- Distinct from `token_budget`: a cheap model can do thousands of cheap turns under a high token
  budget while making zero progress — the turn counter catches *count*, the detector catches
  *repetition*, the budget catches *spend*. Three orthogonal bounds.
- Trips → `no_progress`, suspend, emit. Resume resets the counter.

### Why this closes M5

| bound | catches | independent of |
|---|---|---|
| progress detector (SHA-256 fingerprint, warn-then-block) | semantic spinning (same actions/output) | token budget, turn count |
| `max_self_continuations` (default 25) | runaway *count* even with novel-but-useless turns | token budget |
| `token_budget` (default off) | raw spend (safety net) | progress |

The detector ties directly to the **trace store off-track detection** (Decision #17): the loop
already writes off-track markers + user-pushback signals to trace at `pre/post_tool` and `turn_end`.
The progress detector should **consume trace off-track annotations as a second stuck-signal source**
(an off-track-marked turn counts toward the no-progress window) rather than computing trajectory
quality from scratch — SSoT: trace owns trajectory annotation, goal owns the continuation decision.

### Non-convergence e2e (Eng L9 / M5)

Add a negative-path e2e row: set an **unsatisfiable** goal under `autonomy=auto`; assert the agent
self-continues a bounded number of times then **halts with `no_progress`** (not infinite, not budget
exhaustion) and emits `goal.suspended(reason=no_progress)`. Pass = bounded halt + correct reason
surfaced; fail = runs to budget/forever. This complements e2e #8 (the happy-path self-continue) and
e2e #14 (autonomy=off halts).

---

## Prefix + autonomy integration

### Goal in the frozen prefix (Decision #21/#29)

The system prompt is assembled **once at session_start and frozen** (memory retrieval + skill L0 +
persona + cache breakpoint at end of static prefix; never mutated mid-session). The active goal's
`objective` is injected into this static prefix at `session_start` — so the goal is part of the
cached prefix and costs nothing on continuation turns (cache-hit). This is why goal injection MUST
be a `session_start` hook, not a per-turn injection: a per-turn goal injection would **break the
frozen prefix** and bust the cache (Decision #29: "if cached_tokens stays 0 → WARNING").

Consequence — **`set` / `refine` mid-session do NOT change the prefix this session.** They write the
goal row, but the *injected* objective text is frozen until the next session_start (same semantics as
memory/skill edits taking effect next session, Decision #12/#17a). The current session keeps driving
on the original injected objective; the refined text is picked up on the next session_start re-read.
This is the correct, cache-safe behavior and must be documented as expected (Fail-Loud: not a bug).

The **continuation message** ("continue toward: <objective>"), by contrast, is a *turn input* (a
fresh user-role message appended after the frozen prefix) — it does not touch the prefix and is
cache-neutral. So the live objective text the model sees each continuation turn rides in the
mutable suffix, while the frozen copy anchors the cache. (If refine happened mid-session, the
continuation suffix can carry the updated text even though the prefix is stale — acceptable, the
suffix is the operative instruction.)

### Autonomy gate integration (Decision #20c)

Goal self-continuation is one of the four auto-loops governed by the global `autonomy` switch
(goal / distill / evolve / dream). Mapping at the `turn_end` continuation decision:

| autonomy | goal self-continuation behavior |
|---|---|
| `off` | **no self-continuation.** turn_end subscriber returns early; goal stays `active` but dormant — resumes driving when autonomy flips back. (e2e #14: "after off, goal no longer self-continues.") |
| `assist` (**default**) | self-continuation **requires confirmation** — emit a `goal.continue_proposed` event; CLI/UI prompts the user; on yes → inject; on no/timeout → stay active, dormant. |
| `auto` | self-continue automatically (subject to M5 progress bound + budget). This is the autonomy level where M5 matters most — hence the progress detector is non-negotiable, not optional. |

Two-layer relationship: autonomy gate is the **outer** e-stop (does any self-driving happen at all);
the M5 progress bound + budget are the **inner** convergence guards (when self-driving IS allowed,
does it stop spinning). The gate is checked before the progress detector in the decision sequence
(no point computing fingerprints if we're not allowed to drive). The progress/budget suspension
(`no_progress`/`budget_limited`) is independent of autonomy — even in `auto`, a stuck goal halts.

L8 dependency: the autonomy gate must land with/before the first auto-loop. Since goal is the first
self-driving loop a user typically exercises (e2e #8), goal's implementation depends on the autonomy
gate existing — sequence autonomy gate before goal in the build order.

---

## Industry refs with URLs

- Codex `/goal` mode — persistent objectives, token budgets, autonomous continuation, `set_goal`
  self-report tool, per-thread persistence (`codex-rs/state/src/runtime/goals.rs`):
  https://codex.danielvaughan.com/2026/04/16/codex-cli-goal-mode-persistent-objectives-token-budgets/
- Codex `/goal` overview — verbs (set/view/pause/resume/clear), status values (active/paused/
  budget-limited/complete), server re-trigger, v0.128.0 / 2026-04-30, `goals` feature flag:
  https://www.howdoiuseai.com/blog/2026-05-05-openai-codex-goal-the-new-long-horizon-mode-for
- Codex `/goal` as a first-class Ralph loop, completion semantics:
  https://ralphable.com/blog/codex-goal-command-ralph-loop-openai-built-in-autonomous-coding-agent-2026
- Hermes-agent SHA-256 tool-call loop guard (issue #481) — fingerprint = SHA256(tool_name+args),
  window 10, threshold 3, ping-pong A-B-A-B, max_warnings_before_block 2, tool-exempt list,
  input-only hash: https://github.com/NousResearch/hermes-agent/issues/481
- Ralph loop — MAX_ITER (default 10), completion-marker-file stop condition, progress.txt/prd.json
  state, "Codex /goal = Ralph as first-class primitive": https://github.com/snarktank/ralph
- Loop engineering / no-progress consensus — same error/empty-diff/failing-test N times in a row;
  loop fingerprinting + cost budget + no-progress, not max-iter alone:
  https://explainx.ai/blog/loop-engineering-coding-agents-claude-code-guide-2026
- AI agent circuit breakers — self-terminate on threshold breach, operates independently of human
  presence: https://dev.to/waxell/ai-agent-circuit-breakers-the-reliability-pattern-production-teams-are-missing-5bpg
- Agents loop forever — fingerprint of last tool call + last result; stop when same fingerprint N×:
  https://matrixtrak.com/blog/agents-loop-forever-how-to-stop
- Result-aware loop detection (include output in stuck signal — informs the trace off-track tie-in):
  https://github.com/zeroclaw-labs/zeroclaw/issues/2152

---

## Open questions

1. **`thread_id` derivation in Alfred.** Codex keys goals on `thread_id` (a conversation lineage,
   not a single session). Alfred's session store is per-session (Decision #4b). Need to define the
   thread/lineage id that `--continue` preserves so the goal row is re-found. Likely the root
   session_id of a `--continue` chain. Must be decided with the session-store module.
2. **Turn-level fingerprint stability.** `serialized_args` and `assistant_text_normalized` must
   normalize deterministically (sorted keys, whitespace/timestamp stripping) or the detector
   under-fires. Needs a canonicalization spec shared with the trace store (which also fingerprints).
3. **`set_goal(complete)` trust under `auto`.** A hallucinated `complete` ends the goal prematurely.
   Do we add an optional code-side verification step (e.g. a goal-completion check tool / test run)
   before honoring `complete`, or accept model self-report as MVP and rely on the user to re-`set`?
   Recommend: MVP trusts the report (matches Codex); leave a `completion_verifier` hook for later.
4. **Budget counter writer.** `tokens_used`/`time_used_seconds` accumulate on the goal row — which
   component increments them? Cleanest: the goal `turn_end` subscriber reads the just-finished turn's
   usage from the trace/session record and adds it. Confirms goal stays a pure consumer (no loop
   coupling), but depends on per-turn usage being available at turn_end.
5. **`assist`-mode confirmation UX for self-continuation.** Confirming every single continuation turn
   defeats long-horizon autonomy. Options: confirm-once-then-auto-for-N, or a batch grant
   ("continue up to 10 turns"). Needs the autonomy/config module + a CLI/TUI affordance.
6. **Interaction with handoff/subagent.** If a self-continuing goal spawns a subagent (Decision #23),
   does the subagent's turns count toward `max_self_continuations` and the progress window? Proposed:
   subagent turns are isolated (Decision #23a) and counted in the subagent's own ledger, NOT the
   parent goal's progress window — but a goal making progress *only* by delegating must still be seen
   as progressing. Needs reconciliation with the trace store's subagent-trace records.
