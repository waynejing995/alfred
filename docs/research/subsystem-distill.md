# Ring-3 Subsystem — distill (trace2skill)

Date: 2026-06-15
Module: `agentkit/subsystems/distill/` (Ring-3 plugin, event-driven)
Spec refs: §5.3 (Ring-3 table), §6.1 (autonomy), Decisions #16, #17, #18, #20c, #24, #31; e2e #9, #14.
Primary sources: Trace2Skill (arxiv 2603.25158, parallel-fleet consolidation) + Trace2Skill EDA (arxiv 2605.21810, oracle–mutator–selector). See ## Industry refs.

---

## Module scope

distill is **trace2skill**: it mines a BATCH of execution traces in parallel and induces
new, conflict-free skills, writing each as a `SKILL.md` into the highest-precedence skill
root. It is the system that grows Alfred's skill library from lived experience.

In scope:
- Subscribe `idle` / `tick` (NEVER `turn_end` — no reactive per-trajectory distill, Decision #18).
- Select a diverse batch of traces from the trace store (Ring-2, Decision #17).
- Dispatch a parallel fleet of analyst sub-tasks → extract trajectory-local lessons → patches.
- Hierarchically consolidate patches into a conflict-free skill proposal via inductive reasoning.
- Run the **confirm-required new-skill gate** (Decision #20c): propose → user review → write.
- Write the accepted `SKILL.md` (Claude-Code-compatible frontmatter, Decision #24) to the top root.
- Call models ONLY via an injected `ModelProvider` (Decision #16) — never reaches into the loop.

Explicitly OUT of scope (peer boundaries, no mutual calls):
- **Memory housekeeping** (dedup/merge/re-index/decay) — that is **dream** (S4, Decision #18).
  distill does not touch memory; dream does not touch skills. Hard split.
- **Mutating existing skills toward a fitness target** — that is **evolve** (S5, Decision #18a).
  distill *creates* (and may *deepen*) skills from raw trace lessons; evolve *optimizes* an
  existing skill against a replay/score set. distill output is evolve's raw material (Decision #20a).
- **Trace writing** — the loop writes traces at `pre/post_tool` + `turn_end` (Decision #17);
  distill is a pure reader of the trace store.

The two papers map onto the two Alfred subsystems cleanly, and the spec already split them:
**2603.25158 (parallel-fleet → conflict-free directory) IS distill.** **2605.21810
(oracle–mutator–selector generational EA, fitness-scored) IS evolve.** This doc builds
distill on the 2603.25158 algorithm and notes the evolve handoff where they touch.

---

## Trace2Skill algorithm (2603.25158, the distill algorithm)

Three stages. distill owns stages 2–3; stage 1 (trajectory generation) is just normal
agent operation, already captured in the trace store.

### Stage 1 — trajectories (already in the trace store, not run by distill)

Normal agent runs produce trajectories `τ = (q, (r₁,a₁,o₁), …, (r_T,a_T,o_T), y)` —
query, reasoning/action/observation triples, and an outcome label `y∈{success, failure}`.
In the paper these are generated against an "evolving set" `D_evolve`; in Alfred they
accrue organically as the agent works. The trace store's success/failure/pushback/off-track
annotations (Decision #17) ARE the `y` labels and the failure-mode signals distill needs.
Paper scale: ~200 trajectories, 1 per task; <2 GPU-hours to generate at 122B.

### Stage 2 — parallel fleet of analyst sub-agents → patches

A fleet of independent analysts each receive **a frozen copy of the current skill `S₀`** and
**a single trajectory**, and emit a **skill patch** `pᵢ`. Two analyst archetypes, split by `y`:

- **Success analyst `𝒜⁺`** — single-pass. Cleans the trajectory, identifies *generalizable*
  behaviour patterns that produced the correct answer, proposes patches that reinforce them.
- **Error analyst `𝒜⁻`** — multi-turn ReAct loop with artifact access. Inspects the full trace,
  reads input/output files, compares the agent's answer against ground truth, **iteratively
  narrows to a root cause**. Terminates on a validated causal explanation OR turn-budget
  exhaustion (paper: 100 steps). **Trajectories without a validated root cause are dropped**
  from the patch pool — a quality gate, not an error.

Key isolation invariant: *all analysts operate on a frozen `S₀` with zero visibility into
each other's patches.* No sequential dependency → embarrassingly parallel. Paper: **W=128**
analysts in parallel.

**Patch format** = a structured edit set against the skill directory: a list of operations,
each `{file, op, anchor, content}` where `op ∈ {insert_after, replace_range, create_file, …}`.
This is a *diff proposal*, not free-form prose — that is what makes Stage 3 conflict-checkable.

### Stage 3 — hierarchical consolidation → one conflict-free skill

All patches `𝒫 = 𝒫⁺ ∪ 𝒫⁻` are merged in a tree over `L = ⌈log_{B_merge}|𝒫|⌉` levels:

```
p^(ℓ+1) = ℳ(π_θ, S₀, {p₁^(ℓ), …, p_{B_merge}^(ℓ)})
```

`ℳ` is an LLM merge operator (same provider `π_θ`), `B_merge` = merge batch size (paper: **32**).
For ~70 error lessons this is ≈7 sequential merge rounds; wall-clock ≈3 min vs ≈60 min for a
sequential one-patch-at-a-time baseline (~20× speedup — this is *why* distill is batch/parallel,
Decision #18).

The merge operator does **inductive reasoning by prevalence**: it is explicitly instructed to
keep edits that **recur across independent patches** (recurring observations reflect systematic
task properties → generalize), and **discard idiosyncratic edits** that appear in only a few
patches (task-specific noise / model quirks). This prevalence filter is the core defense against
overfitting to one trajectory — and the reason batch mining beats reactive per-turn distill.

Output: a single updated skill `S = (M, ℛ)` — `M` = `SKILL.md` (procedural knowledge in NL:
when to apply, step-by-step strategy, known failure modes), `ℛ` = auxiliary resources
(`references/` case-specific heuristics, scripts, assets). Broad principles live in `SKILL.md`,
case detail in `references/` — this maps 1:1 onto Alfred's L1/L2 progressive disclosure.

### Deepen vs create (both supported)

- **Create**: `S₀` = a thin draft `π_θ` wrote from parametric knowledge alone (or empty stub).
  Trajectory-driven evolution then does the real authoring → genuinely new skill. This is the
  default distill path and the one behind e2e #9.
- **Deepen**: `S₀` = an existing skill (human- or distill-authored). Same pipeline refines it
  with failure-specific guidance + reinforced strategies. NOTE: deepening an *existing* skill is
  the seam where distill and evolve nearly touch — see ## Decoupling for the rule that keeps them
  peers (distill deepens from *fresh trace lessons*; evolve optimizes against a *replay score*).

### Why the result transfers (the evidence behind Decision #17)

Skills evolved by a 35B model on its own traces lifted a 122B agent by **+57.65 pts on
WikiTableQuestions**, and beat Anthropic's official xlsx skills on SpreadsheetBench. The skill
is used **directly at inference with no retrieval index** — it just becomes part of the loaded
skill catalog. This is the strongest harness>model evidence and the reason trace2skill is rated
one of the 3 highest-leverage modules.

---

## Trigger + batch + selection

### Trigger (when distill runs)

Subscribe **`idle`** and **`tick`/`job_due`** (async dispatch — never blocks the loop), **never
`turn_end`** (Decision #18: no reactive per-trajectory distill — a single trajectory cannot give
the prevalence signal Stage 3 depends on). Two host lifetimes (Decision #6):

- Interactive CLI: `idle` fires when the user goes quiet; distill may run opportunistically.
- Headless daemon: `tick`/`job_due` from the scheduler drives periodic mining (the real home —
  e2e #9 runs `alfred` daemon). Cron's fresh-session model (Decision #6, H3) means each distill
  run is its own short-lived session; its output takes effect on the next session that reloads
  the skill catalog (skills frozen at session_start, Decision #12 — no hot-reload mid-session).

**Gate FIRST (L8, Decision #20c):** every trigger checks the global `autonomy` switch before
doing any work. `off` → halt (DEBUG log, no model calls). `assist`/`auto` → proceed to the
confirm-required gate (below). e2e #14 asserts distill does not trigger under `autonomy=off`.

**Batch threshold, not every idle:** distill should fire only when there is *enough new
material*. Trigger condition = `unmined_trace_count ≥ batch_min` (config, default ~50, tracking
the paper's ~200-trajectory evolving-set scale but lower for an MVP). Below threshold → silent
skip (DEBUG), same signal-to-noise discipline as evolve's missing-trace filter (Decision #20b).
A high-water mark (last-mined trace id) in distill's own small state file makes "unmined" cheap
to compute and prevents re-mining the same traces.

### Batch size + parallelism config

| Param | Paper | Alfred MVP default | Notes |
|---|---|---|---|
| `batch_min` (trigger) | ~200 evolving set | 50 | min unmined traces to fire at all |
| `batch_size` (per run) | up to ~200 | 50–100 | how many traces one distill run mines |
| `fleet_width` W (analysts) | 128 | bounded by provider concurrency (e.g. 8–16) | parallel analyst sub-tasks |
| `merge_batch` B_merge | 32 | 16–32 | fan-in per merge-tree level |
| ReAct turn budget (`𝒜⁻`) | 100 | 30–50 | error-analyst root-cause loop cap |

Fleet width is the one place MVP must diverge hard from the paper: 128 concurrent provider
calls is unrealistic against a rate-limited proxy gateway (Decision #26). Run the fleet through
a bounded `asyncio.Semaphore`; the consolidation tree (Stage 3) doesn't care how wide Stage 2
actually ran. **Subagent reuse:** each analyst is naturally a subagent (S1) with an isolated
context + its own trace — reuse the subagent primitive (Decision #23a isolation) rather than
inventing a parallel-call mechanism (Delete>Add).

### Selecting WHICH traces to mine (diversity is the whole point)

The paper's power comes from a **diverse pool** — comparing many traces isolates reusable
patterns from task-specific noise. Selection policy (in priority order):

1. **Both outcomes.** Include success (`𝒯⁺`) AND failure/pushback (`𝒯⁻`) — error analysts mine
   failure modes, success analysts mine winning patterns. A success-only batch produces shallow
   skills; failure traces are where the high-value lessons live (matches evolve's "mine success
   AND failure" finding, Decision #18a).
2. **Diversity over recency.** Don't just take the newest N. Cluster candidate traces (cheap:
   by tool-sequence signature / task embedding) and sample across clusters so the batch spans
   distinct task types — prevalence-by-merge only generalizes if the pool is varied.
3. **Unmined only.** Above the high-water mark; never re-mine.
4. **Validated traces only for `𝒜⁻`.** Failure traces where the error analyst can't reach a
   validated root cause are dropped (paper Stage 2) — silent, not fail-loud.

A trace that is corrupt/unreadable → fail-loud (Decision #17 / M7 is for skills; trace-read
failure raises per §7). A trace that is merely uninteresting → silent skip.

---

## Conflict-free consolidation

This is the crux: distill writes to a root that **already has skills**, so it must not emit a
skill that contradicts existing ones (or itself). Defense is layered — programmatic guardrails
+ inductive merge + identity rules — matching the paper's "programmatic conflict prevention."

### A. Programmatic guardrails (deterministic, in the merge operator)

Three hard checks, applied during Stage 3, no LLM judgment:

1. **Reject dangling references** — a patch targeting a non-existent file is dropped.
2. **Withhold line-range collisions** — two patches editing the same line range of the same file
   are flagged as a conflict and withheld (not silently merged) → surfaced for the LLM merge step
   or for the human gate.
3. **Format-validate the result** — the consolidated `S` is run through a **skill-format checker**
   before it can be proposed. This is the same validator the skill-loader uses (M7: corrupt skill
   = WARNING-skip). distill must NEVER emit a skill that the loader would reject — validate at
   author time, fail-loud here rather than skip-at-load later.

### B. Inductive merge (LLM, prevalence-driven)

The merge prompt: *keep prevalent edits; when patches propose the same/similar edit, keep the
best version; when patches propose contradictory edits to the same section, choose the one with
stronger justification OR synthesize both; discard idiosyncratic one-off edits.* Prevalence is
the statistical conflict-resolver — a contradiction that appears once loses to a pattern that
recurs. This handles *intra-batch* conflict (patch vs patch).

### C. Conflict with the EXISTING catalog (the Alfred-specific part the paper understates)

The paper's `S₀` is a single skill; Alfred has a **multi-root catalog with name-as-identity**
(Decision #12). Two extra rules:

1. **Feed the existing L0 catalog into Stage 3 as context.** The merge operator and the
   gate-proposal step must see the names+descriptions of all currently loaded skills so it can
   decide: is this a NEW skill, or a DEEPEN of an existing one? A near-duplicate-name or
   near-duplicate-description proposal should become a *deepen* patch against the existing skill,
   not a second skill that would trip the same-name shadow WARNING (Decision #12) at next load.
2. **Respect precedence + write target.** distill writes new skills to the **highest-precedence
   root** (`./skills`, §5.3). If the proposed name collides with a skill in a *lower* root, that
   is intended override semantics (higher wins + WARNING — Decision #12), but distill should say
   so in the proposal so the human isn't surprised. If it collides with a skill in the *same*
   top root, it MUST be a deepen (version bump via `.versions/`, Decision #20a-i) not a clobber —
   route through the skill store's per-skill write lock + atomic active-`SKILL.md` swap (M4).

### Net: three conflict layers

| Layer | Catches | Mechanism |
|---|---|---|
| Programmatic | dangling refs, line collisions, malformed output | deterministic checks + format validator |
| Inductive merge | patch-vs-patch contradiction, noise | prevalence + stronger-justification rule (LLM) |
| Catalog identity | new-vs-existing skill, cross-root shadowing | L0 catalog as merge context + precedence rules + write lock |

---

## Gate flow (new-skill = confirm-required by default)

Decision #20c, gate (3): **propose → user review → write.** Default `confirm-required`; the
global `autonomy` switch is the outer e-stop.

```
idle/tick fires
  └─ gate check: autonomy == off?            → DEBUG "distill halted", stop      (e2e #14)
  └─ enough unmined traces (≥ batch_min)?     → no: DEBUG skip, stop
  └─ select diverse batch (success+failure, unmined)
  └─ Stage 2: parallel analyst fleet → patches
  └─ Stage 3: hierarchical merge → candidate SKILL.md  (+ programmatic conflict prevention)
  └─ format-validate candidate                → invalid: ERROR (fail-loud), stop
  └─ PROPOSE:
       emit  distill.proposed  event { skill_name, target_root, is_new|is_deepen,
                                        diff_or_full_md, source_trace_ids, shadow_warning? }
  ├─ autonomy == auto:   auto-accept → WRITE
  └─ autonomy == assist (default): hold proposal, wait for user decision
        ├─ accept → WRITE
        └─ reject → discard (advance high-water mark anyway so it isn't re-proposed)
  └─ WRITE (on accept):
       acquire skill-store per-skill write lock (M4)
       is_new  → create <top_root>/<name>/SKILL.md
       deepen  → archive current to .versions/ (Decision #20a-i), atomic swap new SKILL.md
       emit  distill.written  event { skill_name, version, root }
       (takes effect next session that reloads the catalog — skills frozen at session_start)
```

Surfacing the proposal: `distill.proposed` is a plugin event (prefixed namespace, Decision #9),
carried over the event-bus → SSE (`/events`) and rendered by CLI / future TUI. The human decision
comes back as a normal command (`/distill accept <id>` | `/distill reject <id>`), NOT a new kernel
primitive — the gate is just a held proposal keyed by id. In `assist` mode a daemon with no
attached human simply leaves proposals pending (visible in `/distill list`); they are not lost.
e2e #9 walks exactly this: proposal appears → confirm → new `SKILL.md` lands in the top root.

Payload carries **references + metadata only** (Decision #7): `source_trace_ids`, not full trace
bodies; the reviewing UI fetches detail on demand. The proposal SHOULD include provenance
(`origin: distill`, source trace ids) so evolve/version tooling later knows where the skill came
from (Decision #20a origin metadata).

---

## Output = SKILL.md in Alfred's format (skill-loader tie-in)

distill's product is a directory the skill-loader (Decision #12, #24) can load with zero
adaptation. Format = **Claude-Code-compatible frontmatter** (Decision #24) so it sits next to the
bundled wayne-* skills identically.

```
<top_root>/<skill-name>/
  SKILL.md            # L1 body; YAML frontmatter + markdown procedural knowledge
  references/         # L2 case-specific heuristics, examples (paper's ℛ)
  scripts/            # optional executable resources
  .versions/          # loader-INVISIBLE archive (deepen → old SKILL.md parked here, Decision #20a-i)
```

`SKILL.md` frontmatter (the loader keys identity on `name`, Decision #12):

```yaml
---
name: <kebab-case-name>          # SSoT identity; must be unique in the top root
description: <when-to-use>        # L0 index entry — always in prompt; must be discriminative
allowed-tools: [...]             # optional; Claude-Code-compatible
origin: distill                  # provenance (Decision #20a) — not a CC field, Alfred extension
source_traces: [<ids>]           # provenance for evolve/audit
version: 1                       # bumped on deepen; .versions/ holds history
---
```

Body structure follows the paper's skill anatomy and Alfred's L0/L1/L2 disclosure:
- **When to apply** (maps to the L0 `description` discriminator).
- **Step-by-step strategy** (the generalizable behaviour patterns success analysts found).
- **Known failure modes / pitfalls** (the root causes error analysts validated).
- Heavy case detail pushed into `references/` (L2) to keep `SKILL.md` (L1) lean.

Constraints distill MUST honor at author time:
- Run the loader's format checker before proposing (guardrail A.3 / M7). Never emit something the
  loader would WARNING-skip.
- `description` must be discriminative — it is the only thing in L0; a vague description means the
  skill never triggers. (The paper's "when to apply a technique" is exactly this field.)
- `name` unique in the top root; cross-root same-name is intentional override (Decision #12).
- On deepen, write via the skill store's atomic swap + `.versions/` archive (M4, #20a-i) — never
  edit `SKILL.md` in place.

---

## Decoupling (distill ⟂ dream ⟂ evolve)

Ring invariant (§2): Ring-3 works only via hooks/events/registries; never injects inward and —
the spec's extra constraint — **no mutual calls between Ring-3 peers** (Decision #18). distill is
independently disable-able and A/B-swappable (e2e premise; CEO Tier-0 keeps it).

**distill vs dream (hard split, Decision #18).** Disjoint write targets are the entire decoupling:
distill writes **skills**, dream writes **memory**. distill never dedups/merges/decays memory;
dream never authors/edits skills (e2e #16 asserts "skills untouched by dream"). Both may subscribe
`idle` but do separate work on separate stores → no contention, no ordering dependency. If
coordination is ever needed it is *event-only*: dream emits `dream.consolidated`; distill MAY
optionally subscribe (e.g. "memory was just re-indexed, a good moment to mine") — a one-line
`on("dream.consolidated")` subscription, never a function call into dream. Default: no subscription.

**distill vs evolve (peers; distill output feeds evolve, Decision #20a).** This is the subtle one
because both produce/modify skills:
- **distill = author from raw trace lessons** (parallel-fleet consolidation, 2603.25158). Output
  = a NEW (or freshly deepened) skill, gated confirm-required, provenance `origin: distill`.
- **evolve = optimize an EXISTING skill against a fitness/replay score** (oracle–mutator–selector
  generational EA, 2605.21810). Triggered by `skill_used` accumulation + trace failure signals,
  gated merge confirm-required.
- The handoff is **store-mediated, not call-mediated**: distill writes a skill to the store; later,
  evolve picks it up as a candidate (distill output IS evolve's raw material, Decision #20a). They
  never invoke each other. evolve may modify distill-produced skills by default (#20a); the
  `origin`/`version` metadata distill writes is what lets evolve and revert reason about lineage.
- **The deepen overlap** is resolved by *signal source*, not by code coupling: distill deepens
  using **fresh trace lessons it just mined** (prevalence over a new batch); evolve deepens using
  a **replay score over a historical set** (mutate→score→keep-best). Same store, same atomic-swap
  write path (M4), different trigger + different evidence. No mutual call.

**distill vs the loop / kernel.** distill calls models only through its injected `ModelProvider`
(Decision #16) — the analyst fleet and merge operator are provider calls, never loop calls. It
reads the trace store, writes the skill store, emits/consumes events. That is the whole coupling
surface. Adding/removing distill touches no kernel code (Ring invariant).

**Coordination, if ever required**, is exclusively via plugin events on the bus (Decision #9):
`distill.proposed`, `distill.written`, and optional inbound `dream.consolidated`. Prefixed
namespace; distill owns its event schemas; no shared mutable state with any peer.

---

## Industry refs

- Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills — https://arxiv.org/abs/2603.25158 (HTML: https://arxiv.org/html/2603.25158v1). **The distill algorithm** — parallel analyst fleet → trajectory-local lessons → hierarchical conflict-free consolidation by prevalence; +57.65 pts cross-scale transfer; beats Anthropic xlsx skills.
- Trace2Skill: Verifier-Guided Skill Evolution for Long-Context EDA Agents — https://arxiv.org/abs/2605.21810 (HTML: https://arxiv.org/html/2605.21810v1). **The evolve algorithm** — oracle (lessons from traces) → mutator (child skills) → selector (SelectQ fitness, pass-dominant + dense tie-breakers); sanitized dense verifier feedback; task-wise generational EA. Cited here to draw the distill/evolve boundary.
- SkillRL: Evolving Agents via Recursive Skill-Augmented RL — https://arxiv.org/pdf/2602.08234 (https://github.com/aiming-lab/SkillRL). Experience-based distillation; General vs Task-Specific skill split — corroborates batch-over-single-trace distillation.
- MIND-Skill: Quality-Guaranteed Skill Generation via Multi-Agent Induction and Deduction — https://arxiv.org/html/2605.08670v1. Induction+deduction quality gate — corroborates the format-validate + root-cause-validation guardrails.
- Agent Skill Evaluation and Evolution: Frameworks and Benchmarks — https://arxiv.org/html/2606.11435. Survey framing of the distill/evolve landscape.
- Claude Code Skills (SKILL.md frontmatter) — https://code.claude.com/docs/en/skills ; best practices https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices. The output format Alfred is compatible with (Decision #24).

---

## Open questions

1. **Diversity selection cost.** Clustering traces by tool-signature/embedding to sample a diverse
   batch adds a step the papers gloss (they use a curated evolving set). MVP could start with
   "all unmined success+failure above threshold" and add clustering only if skills come out
   overfit. Needs an A/B via the eval harness (module #17): diverse-sample vs recency-take.
2. **Fleet width vs proxy rate limits.** W=128 is infeasible against the gateway (Decision #26).
   What semaphore width keeps consolidation quality while respecting limits? Empirical — tie to
   the eval harness cost-delta measurement.
3. **Daemon proposal lifetime (H3).** In a long-lived `assist` daemon with no attached human,
   proposals pile up pending. Need a TTL / max-pending policy so distill doesn't re-mine into an
   unbounded queue. Suggest: cap pending proposals; above cap, stop mining until drained.
4. **Deepen routing heuristic.** "Is this proposal a new skill or a deepen of an existing one?" is
   decided by the merge LLM from the L0 catalog. False "new" → same-name shadow WARNING churn;
   false "deepen" → unwanted edit to a good skill. Needs a similarity threshold + the human gate
   as backstop. Should the gate explicitly show "this looks like a deepen of X — confirm?"
5. **Cross-run prevalence.** The paper's prevalence is *within one batch*. Should distill remember
   lessons rejected as "idiosyncratic" across runs (a lesson seen once per batch over 5 batches IS
   prevalent globally)? Out of MVP scope but a natural upgrade — would need a small lesson ledger.
6. **Failure-trace ground truth.** Error analysts compare against "ground truth"; Alfred traces
   carry user-pushback/off-track annotations, not always a clean correct answer. How robust is
   root-cause validation without explicit ground truth? May limit `𝒜⁻` yield on conversational
   (non-task) traces.
