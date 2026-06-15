# Ring-2 Store — Trace (annotated execution trajectories, agent-learning raw material)

Date: 2026-06-15
Module: `agentkit/stores/trace/` (Ring-2, interface + default impl)
Spec refs: §4 (Stores table), §4.2 (trace store), §5.3 (distill/dream/evolve), §5.2 (handoff),
Decisions #17, #17a, #18, #18a, #20a, #20b, #23; e2e #9, #10, #16; `E2E: none` (trace is internal).
Sibling: `store-session.md` ("Boundary with trace store" — the SSoT split is co-owned there).

> **Why this is the highest-leverage module.** Per Decision #17 the learning signal is the
> *annotated execution trajectory*, not the chat transcript. Trace2Skill (arxiv
> 2603.25158 / 2605.21810): skills evolved by a 35B model on its own trajectories lifted a
> 122B agent **+57.65pts** on WikiTableQuestions. The trace store is the substrate every
> harness>model experiment (distill / evolve / dream) draws from. If the schema is wrong,
> distill mines noise and evolve scores against a meaningless replay set. Get the schema
> and the annotation pipeline right; everything downstream is consumer code.

---

## Module scope

The trace store is the **SSoT for agent-learning raw material**: per-step execution
trajectories enriched with *annotations* (success/failure, user-pushback, correction,
off-track) that make them usable as (a) distill's mining input and (b) evolve's
replay/scoring set. It is written by the loop (and by subagents/handoff workers) and read
**only** by Ring-3 learning subsystems — never by resume/recall (that is the session
store).

In scope:
- Persist every executed **step** as an evaluable record: tool call (name + args), tool
  result/observation, the assistant decision that produced it, outcome, timing, budget.
- Persist **turn-** and **trajectory-level** rollup: final outcome, the labels distill /
  evolve key off of.
- Persist **annotations** — the success/failure/pushback/correction/off-track signals —
  with provenance (`auto` vs `user` vs `verifier`) so a consumer can trust-weight them.
- Persist **skill linkage**: which skills (and which skill *version*) were active / used
  in a trajectory → this is what makes a trajectory a candidate replay row for *that*
  skill (evolve candidate filter, Decision #20b).
- Persist a **pointer back to the session** (`session_id` + message-id range), never the
  message body (Decision #7, #17, §4.2 boundary table).
- Accept writes from **subagents and handoff workers** (Decision #17, #23) — a worker's
  trajectory and the handoff transfer record both land here, feeding evolve.

Explicitly OUT of scope:
- **The conversation record** — lives in the session store (`store-session.md`). The trace
  store references it; it does not copy message bodies.
- **Memory facts** — separate Ring-2 memory store (Decision #17a).
- **`stream_delta`** — transient render projection, persisted nowhere (Decision #15).
- **The replay *executor*** — the trace store *stores* what is needed to replay; evolve
  (S5) owns the rollout harness that actually re-runs and scores. The store is passive.

---

## Trace schema

The schema is **three nested levels** — trajectory → turn → step — mirroring how both
Trace2Skill papers structure a `τᵢ` (a labeled trajectory = "query, reasoning/tool-use
history, final output, binary correctness outcome") and how GEPA samples "trajectories
(reasoning, tool calls, tool outputs)" plus a `(score, feedback)` per rollout. Schema-first
(CLAUDE.md): pydantic types are the contract; SQLite is the swappable impl.

### Level 1 — trajectory (= one rollout / one agent run on one task)

The unit distill mines and evolve replays. **One trajectory ≈ one (sub)agent session's
work toward one task.** For the main agent this is typically one user-task span within a
session; for a subagent/worker it is the whole worker run.

| Field | Type | Why it's load-bearing |
|---|---|---|
| `trace_id` | str (ulid) | trajectory identity |
| `session_id` | str | **pointer** to session store (no body copy, Decision #7) |
| `parent_trace_id` | str? | subagent/handoff worker → its spawner's trajectory (Decision #17, #23) |
| `agent_role` | str | `'main'` \| `'subagent'` \| `'worker'` — so evolve can scope replay sets |
| `task` | str | the query/goal that opened the trajectory (Trace2Skill `query`) |
| `active_skills` | list[SkillRef] | `(name, version)` of every skill in L0 at session_start — the candidate-filter key (Decision #20b) |
| `used_skills` | list[SkillRef] | skills actually expanded to L1/used (`skill_used` event) |
| `outcome` | enum | `success` \| `failure` \| `partial` \| `aborted` \| `unknown` — the **binary-ish label** distill/evolve split on (Trace2Skill `𝒯+` / `𝒯−`) |
| `outcome_source` | enum | `verifier` \| `user` \| `auto` \| `none` — trust-weight of the label |
| `score` | float? | dense scalar in [0,1] when a verifier/judge exists (GEPA `score`; EDA `PassRate`) |
| `feedback` | str? | natural-language outcome note (GEPA `feedback`; EDA "next-focus hint") |
| `started_at` / `ended_at` | float | timing; variance across repeats matters for evolve (EDA `AgentVarianceQ`) |
| `budget_used` | int | tool-call count vs cap — efficiency is a selection tie-breaker |
| `handoff_payload` | dict? | the structured A→B transfer record (Decision #23) when this trajectory is a handoff |

### Level 2 — turn (one user→assistant cycle inside the trajectory)

A thin grouping layer; written at `turn_end`. Carries the turn-scoped annotations (a
pushback usually targets *the previous turn*, not a single step).

| Field | Type | Notes |
|---|---|---|
| `turn_id` / `trace_id` / `seq` | — | ordering within trajectory |
| `assistant_msg_id` | int | pointer to the session `messages.id` (no copy) |
| `turn_outcome` | enum? | per-turn rollup if detectable (else inherits trajectory) |
| timing / token / budget deltas | — | per-turn cost |

### Level 3 — step (one tool call + its result — the evaluable atom)

Written at `pre_tool` (open) and `post_tool` (close). **This is the replay atom** — the
EDA paper's `rollout_diagnostics.jsonl` is exactly a stream of these ("tool sequences,
files read/written, compile commands, verifier calls, final pass/fail, path-grounding
issues, phase reached").

| Field | Type | Why |
|---|---|---|
| `step_id` / `trace_id` / `turn_id` / `seq` | — | deterministic replay ordering (mirror session `seq`) |
| `kind` | enum | `tool_call` \| `reasoning` \| `decision` \| `state` (OTel GenAI span-type analog) |
| `tool_name` | str? | the action taken |
| `tool_args` | dict | **structured** args (the decision's content — distill needs this to induce a skill) |
| `tool_result` | str/dict? | observation; large blobs stored by pointer/digest (see Storage) |
| `result_status` | enum | `ok` \| `error` \| `vetoed` — `error` rows are gold for failure-mode mining |
| `error` | dict? | typed error + retry count (OTel error/retry attrs) |
| `step_annotations` | list[Annotation] | step-local signals (e.g. an immediate self-retry = correction) |
| `latency_ms` / `budget_after` | — | cost/efficiency |
| `msg_id` | int? | pointer to the session row that carried this tool call/result |

A step stores **structured args + result**, not just a rendered string, because distill
induces a *parameterized* skill from them and a trajectory-match evaluator (agentevals)
compares **tool-call name + arguments**, not prose. Reasoning steps are stored
(GEPA/Trace2Skill both treat "reasoning history" as part of `τ`) but are the first thing
to drop to a pointer if size pressure appears.

---

## Annotation capture

This is the part the spec calls out as CRITICAL and where the design earns its keep. An
**annotation** is a typed signal attached to a trajectory / turn / step, with explicit
**source** so consumers can trust-weight it (a label asserted by a verifier ≠ a label
guessed by a heuristic). Schema-first:

```python
class Annotation(BaseModel):
    kind: Literal["success", "failure", "user_pushback",
                  "correction", "off_track", "user_approval"]
    source: Literal["user", "auto", "verifier", "judge"]   # provenance / trust
    confidence: float = 1.0          # auto/judge < 1.0; user/verifier = 1.0
    target: Literal["trajectory", "turn", "step"]
    target_id: str
    evidence: str | None = None      # the span/phrase that triggered it (audit + reflexion)
    detector: str | None = None      # which rule/model emitted it (debuggability)
```

### The label-source ladder (most → least trustworthy)

Decision #17 asks: automatic vs from user feedback? Answer: **both, layered, and tagged**.
A consumer (evolve) trusts `source` in this order; never silently collapses them.

| Source | Trust | How obtained | Maps to |
|---|---|---|---|
| `verifier` | highest | a programmatic oracle returns pass/fail (tests pass, build succeeds, tool exit 0, goal-met predicate) | EDA `pr∈{0,1}` hidden verifier; the *cleanest* label when a task is verifiable |
| `user` | high | explicit user signal in the conversation | the real-world label for chat agents |
| `judge` | medium | an LLM-judge (injected provider, like fusion's #11) scores the trajectory post-hoc | GEPA `(score, feedback)`; medium because judges are noisy |
| `auto` | low | a code heuristic over the trace (error rate, retries, abort) | cheap default so *every* trajectory gets a provisional label |

**Detection mechanics, per signal:**

1. **success / failure**
   - *verifier*: if the task ran a verifiable tool (test runner, compiler, goal predicate),
     its exit/result is the label — this is the EDA paper's gold standard, and Alfred's
     goal subsystem (S6) already has a "goal-met" predicate that can emit `success`.
   - *auto* fallback (every trajectory): `failure` if it ended `aborted` / budget-exhausted
     / final step `result_status=error`; `success` if it reached a clean `turn_end` with no
     trailing error and no pushback; else `partial`/`unknown`. Per Decision #20b an
     unlabeled/low-signal trajectory is **not an error — it silent-skips evolve's candidate
     set (DEBUG)**, so `unknown` is a legitimate terminal state, not a fail-loud case.

2. **user_pushback** — "no, not that" / "that's wrong" / "I didn't ask for that".
   - *auto* (primary): a lightweight detector on the **next user turn** after an assistant
     action — negation+rejection cues, re-issued/contradicting instruction, sentiment flip.
     The research is explicit that "social anchoring bias only shows up when you introduce
     pushback" and that pushback is a *social correction trigger via dialogue repair* — i.e.
     it is detectable from the conversation turn structure, which the loop already owns.
     Tag `source=auto, confidence<1`.
   - *judge* (upgrade): an LLM-judge pass over `(assistant_turn, next_user_turn)` for higher
     precision; tag `source=judge`. MVP can ship auto-only and add judge behind a config.
   - The pushback annotation `target`s the **turn that was pushed back on** — that is the
     turn evolve wants to learn to avoid, and the failure-mode that EDA mines as a negative
     lesson ("REMOVE: prefer editing same-basename mirrors").

3. **correction** — the agent (or user) fixes course after an error/pushback.
   - *auto*: a `user_pushback` (or step `error`) immediately followed by a *different*
     assistant action that then succeeds → the (mistake, fix) pair. This is the highest-value
     mining target: Reflexion-style self-correction converts "success/failure + error trace"
     into "dense actionable natural-language feedback" — distill turns a (mistake→fix) pair
     into a skill rule, exactly EDA's `KEEP/ADD/REMOVE` lessons.
   - target = the corrective step; `evidence` links the preceding mistake step.

4. **off_track** — agent looping, drifting from the task, repeating a failing action.
   - *auto*: cheap structural detectors — same `(tool_name, tool_args)` repeated N× with
     `error`; budget burned with no `outcome` progress (this is Eng finding **M5**'s
     no-progress detector — the trace store is where that signal is recorded); goal-distance
     not decreasing across turns (goal subsystem can emit it).
   - This doubles as a *runtime* signal (the loop / goal driver can read it) and a *learning*
     signal (a trajectory full of off_track steps is a failure exemplar).

5. **user_approval** — explicit "yes, perfect" / accepting a distill/evolve proposal.
   - *user*: high-trust positive label; also the natural hook for the autonomy gates
     (Decision #20c) — a confirmed distill/evolve proposal *is* a user_approval annotation.

**Where detection runs (Push, don't poll):** the loop emits `post_tool` / `turn_end`
events (Decision #7); an annotation detector is an **event subscriber** that writes
annotations as the trace is written — it does not poll the store. Auto-detectors are
synchronous+cheap (string/structural rules); judge-detectors run async (don't block the
loop) and may also run in a **batch backfill** pass (dream/distill's `idle` tick can
re-annotate older trajectories with a better judge — annotations are append-only, so a
later higher-trust label supersedes an earlier `auto` one without rewriting history).

**Fail-loud boundary (CLAUDE.md):** a *missing* annotation is normal (silent-skip, DEBUG —
Decision #20b). A *detector raising* is a real exception → surface it. Never `except: pass`
a detector; an annotation that silently never fires is the swallowed-signal anti-pattern.

---

## Replayability

Decision #18a: evolve generates skill variants and **scores them against a replay set
drawn from traces, mining success AND failure modes**. For that to work the store must make
each trajectory **deterministically re-runnable as a scoring case**. Concretely a
replayable trajectory must store:

1. **The task / initial conditions** — `task`, plus enough environment pointer to
   reconstruct the starting state (working dir, input files referenced, the frozen
   `active_skills` + their versions). Evolve swaps the skill and re-runs the *same* task.
2. **The full ordered step sequence** — `(kind, tool_name, tool_args, tool_result,
   result_status)` per `seq`. This is the agentevals trajectory format (list of OpenAI
   message dicts with `tool_calls{function:{name,arguments}}`), which is what trajectory-match
   scoring consumes. Storing structured args (not prose) is what makes
   `strict/unordered/superset/subset` matching possible.
3. **The reference outcome + score** — `outcome`, `score`, `outcome_source`. A replay is
   scored by comparing the variant's fresh outcome/trajectory against this reference. EDA's
   selector needs exactly this: `PassRate` (from `outcome`) as the dominant term.
4. **The failure annotations** — replay must mine **failure modes**, not just successes.
   A `failure`/`user_pushback`/`off_track`-annotated trajectory is a *negative* scoring
   case: a good skill variant should turn that failure into a success on re-run. This is the
   EDA oracle's "failed children contribute negative lessons" and the dual error-analyst /
   success-analyst split of 2603.25158 (`𝒜−` reads `𝒯−`, `𝒜+` reads `𝒯+`).

**Two scoring modes the store must support (both used by evolve):**

| Mode | Reference needed | How the variant is scored | Source |
|---|---|---|---|
| **Re-rollout** | task + initial conditions + a verifier | re-run the task with the variant skill, run the verifier → fresh `pr∈{0,1}`/score; aggregate over repeats with a lower-confidence bound to penalize variance | EDA `SelectQ = 0.60·F_LCB + 0.20·F̄_progress + 0.20·Q_skill`; repeats per candidate (e.g. 4) → `AgentVarianceQ` |
| **Trajectory-match** | the stored reference trajectory | compare the variant's trajectory against the stored one (strict/unordered/superset/subset) — no live verifier needed | agentevals trajectory evaluators |

Re-rollout is the gold mode but needs a verifiable task (and is expensive: EDA budgets "80
optimization rollouts per task" = 5 gens × 4 candidates × 4 repeats). Trajectory-match is
the cheap fallback for non-verifiable tasks. The store is mode-agnostic — it just must
preserve **task + ordered steps + reference outcome + failure annotations**; evolve picks
the mode.

**Candidate-filter (Decision #20b):** a skill enters evolve's candidate set only if it has
enough replayable trajectories (e.g. ≥K trajectories with `outcome != unknown` where the
skill is in `active_skills`). Below threshold → silent-skip (DEBUG), not fail-loud. The
store exposes this as a query: `replay_set(skill_name, min_outcome_quality)`.

---

## Storage format

**Recommendation: SQLite (index + annotations + rollups) + JSONL trajectory bodies — both,
with SQLite as the SSoT index and JSONL as the bulk step payload.** This mirrors the
session store's "external content" discipline (store the heavy text once, index separately)
and the EDA paper's own choice (`rollout_diagnostics.jsonl` for step bodies). Rationale:

- **SQLite** for: trajectory/turn rows, **all annotations**, skill linkage, outcome/score,
  pointers — i.e. everything you *query* ("give me failure trajectories for skill X with a
  user_pushback annotation"). Reuse the session store's WAL + jitter-retry write helper
  verbatim (`store-session.md` concurrency section) — same multi-process topology (daemon +
  CLI + subagents all writing traces, Decision #17).
- **JSONL** for: the ordered **step bodies** of each trajectory, one file per trajectory
  (`traces/<trace_id>.jsonl`, one step per line, append-only). Why a file, not a `steps`
  table: (a) the step stream is the bulk volume (tool args/results) and is read *whole* by
  distill/evolve (sequential, not random-access) — JSONL is the natural replay format and
  is exactly agentevals' list-of-dicts on disk; (b) append-per-step during a live rollout
  is cheap and crash-safe; (c) it keeps the SQLite file small and fast for the query
  workload; (d) it is the portable artifact you hand to an external analyst fleet
  (Trace2Skill's parallel sub-agents each read a trajectory file).

So: **SQLite row is the queryable head + pointer to the JSONL body.** A trajectory row
stores a `body_path`; large `tool_result` blobs inside the JSONL may themselves be
pointers/digests when huge (e.g. a 1MB file read → store a digest + session msg pointer,
not the bytes).

### Relationship to the session store (Decision #7 pointer rule, §4.2)

**No body duplication.** The trace store references the session store; it never copies the
conversation. The split (co-owned with `store-session.md`):

| | Session store | Trace store (this module) |
|---|---|---|
| SSoT for | conversation *record* | learning raw material |
| Holds the message body | **yes** (canonical) | **no** — holds `session_id` + `msg_id` pointer |
| Holds annotations / labels / scores | no | **yes** |
| Holds replay sets | no | **yes** (derived view over annotated trajectories) |
| Read by | resume, `session_search` | distill / evolve / dream **only** |

A trace step's `msg_id` points at the session row that carried the same tool call/result.
Reconstructing a human-readable transcript = join trace order → session bodies. The trace
store thus stays small and learning-focused; the session store stays the message SSoT.

### DDL sketch (SQLite head; JSONL holds step bodies)

```sql
CREATE TABLE traces (
    trace_id        TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,            -- pointer to sessions.db (no copy)
    parent_trace_id TEXT,                     -- subagent/handoff worker chain
    agent_role      TEXT NOT NULL,            -- main|subagent|worker
    task            TEXT,
    outcome         TEXT,                     -- success|failure|partial|aborted|unknown
    outcome_source  TEXT,                     -- verifier|user|judge|auto|none
    score           REAL,
    feedback        TEXT,
    body_path       TEXT NOT NULL,            -- traces/<trace_id>.jsonl (step bodies)
    started_at      REAL NOT NULL,
    ended_at        REAL,
    budget_used     INTEGER DEFAULT 0,
    handoff_payload TEXT                       -- JSON, when this is a handoff record
);
CREATE INDEX idx_traces_outcome ON traces(outcome, outcome_source);

CREATE TABLE trace_skills (                   -- skill linkage → evolve candidate filter
    trace_id   TEXT NOT NULL REFERENCES traces(trace_id),
    skill_name TEXT NOT NULL,
    version    TEXT NOT NULL,
    was_used   INTEGER NOT NULL DEFAULT 0,     -- active vs actually used (skill_used event)
    PRIMARY KEY (trace_id, skill_name)
);
CREATE INDEX idx_trace_skills_name ON trace_skills(skill_name, version);

CREATE TABLE annotations (                     -- the learning signal, append-only
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id    TEXT NOT NULL REFERENCES traces(trace_id),
    kind        TEXT NOT NULL,                 -- success|failure|user_pushback|correction|off_track|user_approval
    source      TEXT NOT NULL,                 -- user|auto|verifier|judge
    confidence  REAL NOT NULL DEFAULT 1.0,
    target      TEXT NOT NULL,                 -- trajectory|turn|step
    target_id   TEXT NOT NULL,
    evidence    TEXT,
    detector    TEXT,
    created_at  REAL NOT NULL
);
CREATE INDEX idx_annotations_trace ON annotations(trace_id, kind);
```

The `replay_set(skill_name)` query = join `trace_skills` → `traces` (outcome != unknown) →
`annotations`, returning trajectory heads; evolve then streams each `body_path` JSONL.

---

## Trace2Skill structure (how the papers pool trajectories for parallel batch distillation)

This is the reference design for **distill** (S3) and **evolve** (S5) — the trace store's
two primary consumers — and validates the schema above. Two papers, two flavors:

### A. 2603.25158 — parallel analyst fleet → conflict-free merge (this is *distill*)

Three stages, **batch over a pool**, not reactive per-trajectory (matches Decision #18:
"distill is batch/parallel over many traces, NOT reactive per-turn"):

1. **Trajectory generation.** A frozen agent rolls out on the task set with the initial
   skill `𝒮₀`, producing labeled trajectories. Each `τᵢ` = *"the query, reasoning/tool-use
   history, final output, and a binary correctness outcome"* → split into failure set `𝒯−`
   and success set `𝒯+`. **This is exactly the Level-1 trajectory schema above.**

2. **Parallel patch proposal.** A *fleet* of analysts runs in **one parallel round**:
   - **Error analysts `𝒜−`** read `𝒯−` (failures) — a *ReAct-style loop that inspects
     traces and artifacts, compares outputs against ground truth, and validates candidate
     fixes before proposing a patch*.
   - **Success analysts `𝒜+`** read `𝒯+` (successes) — a *single-pass workflow to identify
     reusable behavior patterns*.
   - Each analyst reads `𝒮₀` + its one trajectory and emits a **trajectory-level skill
     patch**. Reported scale: `W=128` workers, ~70 error lessons, all in a single round.

3. **Patch consolidation (conflict-free merge `ℳ`).** Hierarchical merge, batch size
   `B_merge=32` across `⌈log₃₂(N)⌉≈2` sequential layers. The merge does *deduplication,
   conflict resolution, preservation of non-overlapping insights*, with two rules Alfred
   should copy:
   - **Prevalent-pattern bias**: when multiple patches independently propose the same edit
     for the same failure/success class, treat the recurrence as evidence of a systematic
     property (i.e. frequency = confidence).
   - **Line-level independence**: no two edits may target overlapping lines/passages — the
     *programmatic* conflict prevention (the "conflict-free" guarantee Decision #18 wants).

**Design implication for Alfred:** the store must serve a **pool** efficiently —
`replay_set` / `failure_set(skill)` / `success_set(skill)` queries returning many
trajectory heads, each independently streamable (the JSONL-per-trajectory layout is exactly
what lets 128 analysts each grab one file in parallel with zero contention).

### B. 2605.21810 — oracle–mutator–selector over rollout traces (this is *evolve*)

The verifier-guided EDA variant — directly Decision #18a's "mutator–selector, score on
replay, mine success AND failure modes, keep best + version":

- **Rollout trace** = `rollout_diagnostics.jsonl`: *"tool sequences, files read/written,
  compile commands, verifier calls, final pass/fail, path-grounding issues, and phase
  reached."* (= Alfred's Level-3 step stream.) Dense feedback per call returns *"sanitized
  pass/fail, partial test count, failure phase, and a short next-focus hint"* (= `score` +
  `feedback`). Final label `pr∈{0,1}` from a hidden verifier (= `outcome` +
  `outcome_source=verifier`).
- **Oracle** (strong model): mines the task-local rollout pool for **both success and
  failure modes** → updates a cumulative **lesson bank** of `KEEP / ADD / REMOVE`
  directives. *"Summarize the full task-local population so failed children can contribute
  negative lessons."*
- **Mutator**: proposes child skills from `(survivor skill, oracle lessons, lesson bank,
  tool/visibility contract)`.
- **Selector**: `SelectQ = 0.60·F_LCB + 0.20·F̄_progress + 0.20·Q_skill` — PassRate-dominant
  with dense tie-breakers; lower-confidence bound penalizes variance across **repeats** (4
  repeats/candidate). Budget: `5 gens × 4 candidates × 4 repeats = 80 rollouts/task`.
- **Versioning**: lesson bank is markdown, generation-tracked → maps onto Decision
  #20a/#20a-i's `.versions/` archive + revert.

**Design implication:** evolve's scoring needs the store to expose **per-skill,
per-trajectory** outcomes *with repeats* (multiple trajectories for the same task+skill so
variance/LCB is computable). The `trace_skills` table + `score` + repeated `traces` rows
give exactly this.

### GEPA (arxiv 2507.19457, ICLR 2026) — corroborating the `(score, feedback)` atom

GEPA samples *"trajectories (reasoning, tool calls, tool outputs)"*, reflects on them in
natural language, and requires the metric to return `dspy.Prediction(score=…, feedback=…)`.
It keeps a **Pareto frontier** (candidates best on ≥1 instance) rather than a single global
best — beating GRPO by 20% with **35× fewer rollouts**. Two takeaways baked into the schema:
(1) `score` + `feedback` per trajectory is the right atom (natural-language feedback, not
just a scalar reward — "preserve intermediate trajectories and errors in plain text rather
than reducing them to numerical rewards"); (2) per-instance scoring (which trajectory each
variant wins on) is needed for a Pareto/archive upgrade — the store should keep per-trajectory
scores, not just an aggregate, leaving room for the Decision #18a Darwin-Gödel archive upgrade.

---

## Industry refs (URLs)

- Trace2Skill — *Distill Trajectory-Local Lessons into Transferable Agent Skills* (parallel
  analyst fleet, `𝒯+`/`𝒯−`, conflict-free merge, +57.65pts): https://arxiv.org/abs/2603.25158 · HTML: https://arxiv.org/html/2603.25158
- Trace2Skill — *Verifier-Guided Skill Evolution for Long-Context EDA Agents* (oracle-mutator-selector, `rollout_diagnostics.jsonl`, `SelectQ`, lesson bank): https://arxiv.org/abs/2605.21810 · HTML: https://arxiv.org/html/2605.21810
- GEPA — *Reflective Prompt Evolution Can Outperform RL* (ICLR 2026; `score`+`feedback`, Pareto frontier, NL traces): https://arxiv.org/abs/2507.19457 · DSPy GEPA overview: https://dspy.ai/api/optimizers/GEPA/overview/ · repo: https://github.com/gepa-ai/gepa
- DGM / Darwin-Gödel archive (the #18a upgrade target — keep-ancestors, fitness-weighted): referenced via Decision #18a (Sakana, ICLR 2026)
- LangChain **agentevals** — trajectory format (list of OpenAI msg dicts, `tool_calls{function:{name,arguments}}`) + strict/unordered/superset/subset matchers: https://github.com/langchain-ai/agentevals · trajectory-evals docs: https://docs.langchain.com/langsmith/trajectory-evals
- OpenTelemetry **GenAI semantic conventions** (span kinds, tool-call attrs `gen_ai.input.messages`/`output.messages`, content-capture opt-in, PII default-off): https://opentelemetry.io/docs/specs/semconv/gen-ai/ · agent spans: https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/
- OpenInference semantic conventions (trace attrs over OTel, span types tool/reasoning/state): referenced via Braintrust guide below
- Braintrust — *Agent observability: complete guide 2026* (small opinionated schema = span type/inputs/outputs/timing/errors+retries/identifiers): https://www.braintrust.dev/articles/agent-observability-complete-guide-2026
- Arize — *Agent Trajectory Evaluations* (trace/session-level, tool-choice/arg-correctness/order): https://arize.com/docs/ax/evaluate/evaluators/trace-and-session-evals/trace-level-evaluations/agent-trajectory-evaluations
- Reflexion / self-correction (verbal reflection from success/failure+error trace → dense NL feedback → episodic memory; the `correction` annotation): https://zylos.ai/research/2026-05-12-agent-self-correction-reflexion-to-prm
- Anti-sycophancy / pushback detection (social-anchoring bias only surfaces under pushback; detect agreement-reversal patterns) — informs the `user_pushback` auto-detector: https://sycophancy.md/

---

## Open questions

1. **Trajectory boundary in a long session.** One session can span many tasks. Where does
   one trajectory end and the next begin? Candidate rule: a new top-level user task (or a
   `goal` change) closes the prior trajectory. Needs coordination with the goal subsystem
   (S6) and the session-store compaction rule (`store-session.md` OQ#5: discarded raw turns
   on compaction may be trace material). **Lean:** trajectory = one task-span keyed off
   goal/turn structure, written at the `turn_end` that resolves the task.

2. **Pushback detector precision (auto vs judge) for MVP.** Ship auto-only (cheap string/
   structural rules, `confidence<1`) and add an optional LLM-judge re-annotation pass on
   `idle`? Risk: auto false-positives poison evolve's negative set. **Lean:** auto for MVP +
   `confidence` weighting so evolve can threshold; judge behind a config flag. Validate the
   auto-detector's precision before trusting it as a *failure* label (vs merely a flag).

3. **Verifier availability.** Re-rollout scoring (the gold mode) needs a per-task verifier;
   most chat tasks have none, only a `judge` or `user` label. How many Alfred tasks are
   actually re-rolloutable? If few, trajectory-match is the default and the EDA-style
   `SelectQ`/repeats machinery is reserved for verifiable tasks (code/test/goal-predicate).

4. **Replay determinism vs. model nondeterminism.** Re-rolling a trajectory calls a
   (nondeterministic) model, so a "replay" is a *fresh rollout on the same task*, not a
   byte-replay. The store provides task + initial conditions; it cannot guarantee identical
   intermediate steps. EDA handles this with **repeats + LCB** — Alfred should adopt the
   same (multiple rollouts, variance-penalized score) rather than expecting determinism.

5. **Annotation backfill vs. immutability.** Annotations are append-only; a later
   higher-trust label (judge/user) supersedes an earlier `auto`. Do consumers always take
   max-trust, or is there a resolution policy when `user` and `verifier` disagree?
   **Lean:** consumer takes highest `source` trust; log a WARNING on user-vs-verifier
   conflict (a genuinely interesting signal — the user disagreed with the oracle).

6. **Step-body size control.** Large `tool_result` blobs (file reads, web fetches) bloat
   JSONL. Digest+pointer above a threshold (point at the session `msg_id` for the full body)?
   **Lean:** yes — cap inline result size, spill to session-store pointer, keep a digest +
   first/last-N for the learning signal.

7. **Trace retention / GC.** Traces accumulate forever. Who prunes? dream (housekeeping) is
   the natural owner but Decision #18 says dream touches *memory*, not skills/traces. Either
   widen dream's remit to trace GC or add a small retention policy in the store (keep all
   `failure`/`pushback` exemplars + decay redundant `success` ones). Coordinate with the
   dream module research.
