# Subsystem: evolve (skill self-improvement) — S5

Module: `agentkit/subsystems/evolve/` (Ring-3 plugin; event-subscriber + registry entry)
Decisions: #18a (mutator–selector shape), #20a / #20a-i (versioning+revert safety), #20b
(reasonable-trace candidate filter), #20c (merge gate = confirm-required), #16 (Ring-3
invariants). Eng review gap: **M4** (per-skill write lock + atomic active-`SKILL.md` swap).
Primary paper: **Trace2Skill 2605.21810** (verifier-guided EDA variant — oracle/mutator/
selector). Upgrade path: **Darwin-Gödel Machine** (Sakana, arXiv 2505.22954). Cross-ref:
GEPA (arXiv 2507.19457) for reflective-evolution mechanics.

> Note on the two Trace2Skill papers: **2605.21810** ("Verifier-Guided Skill Evolution for
> Long-Context EDA Agents") is the oracle–mutator–selector loop this module implements.
> **2603.25158** ("Distill Trajectory-Local Lessons into Transferable Agent Skills") is the
> *distill* subsystem (S3). Do not conflate — evolve = optimize an existing skill against a
> replay set; distill = induce a new skill from a batch of traces.

---

## Module scope

evolve **optimizes existing skills** against historical trace evidence. It does NOT create
skills (distill's job) and does NOT touch memory (dream's job). One sentence: *pick a skill
that has enough trace and a failure signal → generate variant(s) → score them by re-running
historical tasks with the variant injected → keep the best survivor → version it → propose
the merge for human confirmation.*

**In scope (this module owns):**
- The mutator–selector loop (oracle → mutator → selector), driven by trace
  failure/pushback signals (Decision #18a).
- The **lesson bank** (KEEP/ADD/REMOVE) per skill — evolve's working memory across
  generations.
- The **replay/scoring harness** — the executor the trace store deliberately does *not*
  own (store-trace.md §"the replay *executor* is evolve's, not the store's"). evolve calls
  `trace_store.replay_set(...)` to get rows, then re-rolls and scores them.
- **M4: the skill-store write path** — per-skill async write lock + atomic active-`SKILL.md`
  swap + `.versions/` archive write. (The skill *loader* explicitly defers this to evolve;
  store-skill-loader.md §"M4 … is the skill-store-write module's job.")
- The candidate filter (reasonable-trace requirement, silent-skip per #20b).
- `revert` (e2e #10) — a write-path op sharing the same lock.

**Out of scope (siblings):**
- New-skill creation → distill (S3).
- Memory housekeeping → dream (S4).
- Trace *storage*, annotation capture, `replay_set` query → trace store (Ring 2).
- Active-version *reading* / `.versions/` exclusion from scan → skill loader (Ring 2).
- Daemon "when does a merged skill take effect" → H3 (cron = fresh session; interactive
  daemon = explicit reload). evolve writes the file; the *frozen-at-session_start* rule
  (Decision #12) means the new version loads next session regardless.
- Merge confirmation UX / autonomy gate → control:autonomy+config (#20c). evolve emits a
  proposal event; the gate decides whether to auto-merge or wait for human.

**Trigger:** subscribes `skill_used` accumulation (Decision #18a). evolve does NOT run
per-turn. A skill crossing a usage threshold (e.g. `skill_used` count ≥ N since last
evolve) **enqueues a candidate**; the actual evolve run fires on `idle`/`tick` like distill
(batch, off the hot path) and only after the candidate passes the trace filter (§Candidate
filter). This is push-don't-poll: the `skill_used` emitter accumulates; evolve drains the
queue on idle.

---

## Mutator–selector algorithm

Faithful to Trace2Skill 2605.21810 Algorithm 1, adapted to Alfred's skill/trace model. One
**evolve run targets one skill** and runs `G` generations; each generation maintains a
population of `K` candidate skill variants.

### Data model

```python
class Lesson(BaseModel):
    id: str
    text: str                       # concrete procedural rule, grounded in trace evidence
    op: Literal["keep", "add", "remove"]
    origin_trace_ids: list[str]     # evidence this lesson is grounded in (auditability)
    generation: int                 # when introduced

class LessonBank(BaseModel):        # evolve's per-skill working memory, persisted alongside skill
    skill_name: str
    lessons: list[Lesson]           # the cumulative KEEP set; ADD appends, REMOVE prunes
    generation: int

class Candidate(BaseModel):
    skill_md: str                   # full proposed SKILL.md body (the artifact)
    lesson_bank: LessonBank
    parent_id: str | None           # the survivor it descends from (lineage; DGM upgrade)
    metrics: SelectMetrics | None   # filled after rollout

class SelectMetrics(BaseModel):
    pass_rate: float                # primary reward: fraction of replay rows that pass
    f_lcb: float                    # lower-confidence-bound of dense progress over repeats
    f_progress_mean: float          # mean dense progress (judge/verifier scalar in [0,1])
    q_skill: float                  # skill-health score (lesson coverage, no leakage)
    blocked: bool                   # B(S): invalid / regressed below parent → score -1
```

### The loop (per generation `g`, target skill `S`)

```
P_0 ← [parent S]                      # generation 0 population seeds from the active skill
for g in 0..G-1:
  # 1. ROLLOUT + SCORE each candidate over the replay set, R repeats each
  for cand in P_g:
    traces ← [replay(row, cand.skill_md) for row in replay_set for _ in range(R)]
    cand.metrics ← compute_metrics(traces, cand, parent=S)
    cand.metrics.q ← SelectQ(cand.metrics)

  # 2. SELECT survivor (PassRate-dominant; survivor only AFTER evaluation)
  survivor ← argmax_{cand in P_g} SelectQ(cand.metrics)

  # 3. ORACLE: mine ALL this-gen traces (success AND failure) → lessons
  oracle_out ← oracle(all_traces_g, survivor, lesson_bank)   # KEEP/ADD/REMOVE
  lesson_bank ← update_bank(lesson_bank, oracle_out)

  # 4. MUTATE: build next population from survivor + lesson handoff
  P_{g+1} ← [carry_forward(survivor)]        # parent ALWAYS re-enters (re-evaluated next gen)
  while len(P_{g+1}) < K:
    child ← mutator(survivor, lesson_bank, tool_contract)
    child ← repair_and_sanitize(child)        # strip leakage / dead-tool advice / contradictions
    if mutation_health_ok(child, survivor):
      P_{g+1}.append(child)
    else:
      P_{g+1}.append(carry_forward(survivor))  # rejected child → fall back to parent

# final survivor across all generations is the merge proposal
propose_merge(best_survivor_ever)
```

### Oracle (mine traces → lessons)

- **Input:** all rollout traces from this generation (passing *and* failing), the current
  survivor skill, the cumulative lesson bank.
- **Job:** identify *repeated success modes* (what passing rollouts actually did) and
  *failure patterns* (extracted from failing-rollout diagnostics + the trace store's
  `failure`/`user_pushback`/`off_track` annotations).
- **Output = three ops on the lesson bank:**
  - **KEEP** — durable guidance from prior generations that still holds (re-asserted).
  - **ADD** — a new concrete rule grounded in *this* generation's evidence
    (`origin_trace_ids` cites the rows). Trace2Skill example shape: *"read submodules
    before wiring; use tight compile→run cycles; compile asserts exactly once."* Rules are
    **concrete and procedural**, not vague ("be careful").
  - **REMOVE** — guidance that contradicts observed outcomes (a lesson that was supposed to
    help but the traces show it didn't / it caused regressions).
- **Implementation:** one LLM call via an **injected provider** (Ring-3 rule: never the
  loop), low temperature (Trace2Skill uses GPT-5 @ T=0.0 for the oracle — deterministic
  diagnosis). The prompt carries the failure annotations verbatim as the grounding signal —
  this is the concrete realization of "consume trace failure/pushback signals, not just
  usage counts" (Decision #18a).

### Mutator (lessons → variant skill)

- **Input:** survivor skill, updated lesson bank, the tool/visibility contract (what tools
  the agent actually has — prevents the variant from advising nonexistent tools).
- **Job:** propose `K-1` child SKILL.md variants that integrate the oracle's ADD directives
  while preserving KEEP rules.
- **Implementation:** LLM call via injected provider, **higher temperature** than the
  oracle for diversity (Trace2Skill: Claude Sonnet @ T=0.35). Different worker/judge split
  is natural here — the oracle is a cold analyst, the mutator is a warm generator.
- **`repair_and_sanitize` (deterministic post-process, NOT an LLM call):** strip advice
  referencing unavailable tools, remove internal/eval-harness leakage (the variant must not
  bake in answers it saw during replay scoring — the analogue of EDA's "hidden-harness
  leakage"), drop self-contradictions, salvage the useful semantic parts when possible.
  This is a guardrail against the model writing a skill that overfits the replay set.
- **`mutation_health_ok`:** a cheap structural gate — valid frontmatter, non-empty body,
  doesn't drop a KEEP-critical directive (the `M_keep` coverage gate). Failing children are
  discarded and the parent is carried instead, so the population never shrinks and a bad
  generation can only stall, never regress.

### Selector (PassRate-dominant, LCB over repeats)

The crux. **Pass rate dominates; dense metrics only break ties.** Faithful to Trace2Skill:

```
SelectQ(S) = -1                       if B(S) = 1   (invalid / regressed vs parent)
           = PassRate(S) + ε · U(S)   if B(S) = 0

U(S)  = 0.60·F_LCB + 0.20·F̄_progress + 0.20·Q_skill
ε     = 0.49 / max(R, N_replay, 1)
```

- **`PassRate(S)`** — fraction of replay rows that *pass* under the variant. The official
  reward. `B(S)=1` (regressed below parent baseline / invalid) hard-floors the score to
  `-1` so a regressing variant can never be selected.
- **`ε` is tiny by construction** (`0.49 / max(R, N_replay)`): one extra passing replay row
  always outweighs the *entire* dense-tie-break term `U`. Dense metrics never override a
  real pass-rate difference — they only order candidates that tie on pass rate. This is the
  single most important design property: **don't let a noisy judge scalar overrule a real
  pass.**
- **`F_LCB` — lower confidence bound over `R` repeats** (robustness to lucky single runs):

  ```
  F_LCB = max(0, F̄_progress − 1.96 · σ / √R)
  ```

  σ = stdev of the dense progress score across the `R` repeated rollouts of the same
  candidate. A candidate that passed once by luck (high variance) gets penalized; a
  candidate that passes consistently (low σ) keeps its score. `R` repeats per candidate
  (Trace2Skill uses ~4) is the cost knob — `K·R·N_replay` model calls per generation.
- **`F̄_progress`** — mean dense progress (the trace store's `score` field: an LLM-judge
  scalar or a code verifier's partial-credit number in [0,1]).
- **`Q_skill`** — skill-health: lesson-bank coverage (are KEEP-critical rules present?), no
  leakage, well-formed. Penalizes a variant that won by deleting safety rails.

**Selection invariant:** a candidate becomes survivor *only after* its rollouts complete —
no unevaluated child is ever promoted. The parent is always carried into the next generation
and re-evaluated (guards against replay-set noise making a one-gen survivor look better than
a robust parent).

### Stopping / acceptance

- Run `G` generations OR stop early when the best survivor's `PassRate` fails to improve for
  `p` consecutive generations (patience). YAGNI on fancy convergence detection for MVP.
- **Acceptance gate:** the final survivor is proposed only if `PassRate(survivor) >
  PassRate(active_skill)` on the *same* replay set, by a margin (e.g. ≥ 1 row OR ≥ δ). No
  improvement → no proposal, DEBUG-log "evolve found no improvement for `<skill>`" and
  exit. (Fail-loud is reserved for *exceptions*, not for "didn't beat baseline" — same
  philosophy as the candidate filter, #20b.)

---

## Gap answers (M4 lock)

**The gap (Eng review M4):** versioning gives *recoverability*, not *mutual exclusion*. Two
concurrent writers race on `.versions/` + the active `SKILL.md`:
- two evolve runs on the same skill (e.g. queued twice), or
- evolve-while-distill (distill rewriting a skill evolve is mid-variant on), or
- evolve-while-revert (human revert during an evolve merge).

A naive writer can interleave: writer A reads `manifest.json`, writer B reads the same
manifest, both compute "next version = v4", both write `.versions/v4/` and clobber each
other's active `SKILL.md`. Versioning recovers *a* state but not the *right* one, and the
`manifest.json` history can be left inconsistent with the files on disk.

### Design: per-skill async lock + atomic swap

**Two layers, both required:**

**(1) Per-skill in-process async lock — serializes writers within the daemon.**

```python
class SkillStoreWriter:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()      # guards lazy lock creation

    async def _lock_for(self, skill_name: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(skill_name, asyncio.Lock())

    async def commit_version(self, skill_name: str, new_md: str, origin: str,
                             parent_ver: str | None) -> str:
        lock = await self._lock_for(skill_name)
        async with lock:                          # ← mutual exclusion, keyed by skill name
            return await self._commit_locked(skill_name, new_md, origin, parent_ver)
```

- **Keyed by `skill_name`** (the SSoT identity, Decision #12) — *not* by path, *not* a
  global lock. Two evolve runs on *different* skills proceed in parallel (distill is
  "parallel, conflict-free" per Decision #18; evolve inherits this only across distinct
  skills). Two writers on the *same* skill serialize.
- **Single async owner:** the daemon holds one `SkillStoreWriter`; distill, evolve, and
  revert all go through it. This is the single-writer-path discipline — many sources of
  writes, one serialization point (matches the budget H1 "single-owner async reservation"
  pattern from the kernel module).
- **The lock spans the whole read-modify-write** (read manifest → pick next version → write
  archive → swap active → rewrite manifest), closing the read/compute/write interleave.
- **Why asyncio.Lock and not a thread lock / file lock:** the kernel is single-process
  asyncio (Decision #5/#6). Within the process this is sufficient and correct. *Fail-loud
  caveat (record it):* if a future deployment runs **two daemon processes** over the same
  skills dir, an in-process lock is insufficient — cross-process needs an OS file lock
  (`flock` / lockfile) on `<skill>/.versions/.lock`. MVP is single-daemon, so document this
  as a known boundary, not a silent assumption. (Decision #6 already says headless = one
  host holding the event loop, so single-daemon is the contract.)

**(2) Atomic active-`SKILL.md` swap — write-temp-then-rename.**

The lock serializes *writers*; the atomic swap protects the *reader* (the loader, which is
lock-free and may read at any instant — store-skill-loader.md §M4). `os.replace` is atomic
on POSIX and Windows: the loader sees either the whole old file or the whole new file, never
a torn write.

```python
async def _commit_locked(self, skill_name, new_md, origin, parent_ver) -> str:
    sk_dir   = self.root / skill_name
    vers_dir = sk_dir / ".versions"
    manifest = read_manifest(vers_dir / "manifest.json")   # safe: under the lock

    # 1. archive the CURRENT active version into .versions/<cur>/ (cold backup)
    cur_ver = manifest.active
    archive_current(sk_dir / "SKILL.md", vers_dir / cur_ver / "SKILL.md")

    # 2. compute next version id (monotonic; no race — we hold the lock)
    new_ver = next_version(manifest)        # e.g. "v4"

    # 3. write the new active SKILL.md ATOMICALLY: temp in SAME dir, then os.replace
    tmp = sk_dir / f".SKILL.md.{uuid4().hex}.tmp"
    tmp.write_text(new_md, encoding="utf-8")
    os.replace(tmp, sk_dir / "SKILL.md")    # ← atomic rename; loader never sees a torn file

    # 4. record the new version body in archive + update manifest LAST (commit point)
    (vers_dir / new_ver).mkdir(parents=True, exist_ok=True)
    (vers_dir / new_ver / "SKILL.md").write_text(new_md, encoding="utf-8")
    manifest.active = new_ver
    manifest.history.append(VersionRec(ver=new_ver, ts=now(), origin=origin,
                                       parent=parent_ver))
    write_manifest_atomic(vers_dir / "manifest.json", manifest)   # temp+os.replace too
    return new_ver
```

- **Same-directory temp + `os.replace`** — rename is atomic only within a filesystem;
  writing the temp in the target dir guarantees same-FS. The `.`-prefixed temp name is
  globbed out by the loader scan (store-skill-loader.md drops dotfiles), so a half-written
  temp is never picked up even mid-write.
- **`manifest.json` is the commit point, written last** — if the process crashes between
  step 3 and step 5, the active `SKILL.md` is the new body but the manifest still says
  `active=v3`. Recovery rule: **the on-disk `SKILL.md` is the SSoT for "what loads"
  (Decision #20a-i: active = the file, full stop); the manifest is the history ledger.** A
  startup consistency check can detect `manifest.active`'s archived body ≠ live `SKILL.md`
  and emit a WARNING (fail-loud on drift). The manifest is *also* written atomically
  (temp+replace) so it's never itself torn.
- **Revert reuses the exact same path** under the same lock: `commit_version(skill, body=
  read(.versions/<target>/SKILL.md), origin="revert", parent=<current>)`. Revert is just a
  commit whose body comes from an archived version — so it can never race evolve/distill;
  they all queue on the one lock. (This satisfies e2e #10: "revert restores old version; old
  version still loadable.")

**Interaction with H3 (daemon reload):** the swap makes the *file* correct immediately, but
the running session's skill list is frozen at `session_start` (Decision #12). So a merged
variant takes effect: cron → next fresh session; interactive daemon → next explicit reload.
M4 guarantees the file the next session reads is internally consistent; it does not (and
must not) hot-swap a live session's prompt.

---

## Scoring via trace replay

evolve's selector needs a number per variant. That number comes from **re-running historical
tasks with the variant skill injected** and scoring the outcome. The trace store is passive
(it stores what's needed to replay); **evolve owns the executor** (store-trace.md §Replay).

### Getting the replay set

```python
rows = trace_store.replay_set(skill_name=S, min_outcome_quality=...)   # store query
```

Per store-trace.md, a replayable trajectory row carries: the **task / initial conditions**,
the **ordered steps** (the replay atoms, written at pre/post_tool), the **reference
outcome + score** (`outcome`, `score`, `outcome_source`), and the **failure annotations**.
The store exposes `replay_set(skill_name, min_outcome_quality)` as a derived view over
annotated trajectories scoped to that skill (via `skill_used` linkage + `agent_role`).

### Two replay/scoring modes (mirror store-trace.md §Replay)

| Mode | What evolve does | Score | When |
|---|---|---|---|
| **Re-rollout** (preferred) | actually re-run the task with the variant skill injected, against a verifier/judge → fresh outcome | `pr ∈ {0,1}` per row + dense `score`; aggregate over `R` repeats with `F_LCB` | task is re-runnable + a verifier or judge exists |
| **Replay-judge** (fallback) | feed the variant skill + the historical task into an injected LLM-judge that scores how the variant *would* have handled it, given the recorded reference outcome | judge scalar in [0,1] | task not safely re-runnable (side-effects, external state) |

- **Re-rollout is the real signal** and matches Trace2Skill's verifier-guided design. The
  judge fallback is for tasks that can't be safely replayed (e.g. they mutated external
  state). Both feed the *same* `SelectMetrics`; re-rollout fills `pass_rate` honestly,
  judge-only mode sets `pass_rate` from the judge's pass/fail head.
- **Repeats (`R`) drive `F_LCB`** — this is why the store records enough to deterministically
  replay ordering (`seq`/`step_id`): variance across repeats must come from model
  stochasticity, not from nondeterministic replay.
- **Verifier as optional dense feedback (Trace2Skill `verify_feedback`):** if a code
  verifier exists, it can also be exposed *to the agent during the rollout* as a black-box
  tool (≤ a few calls/rollout; returns sanitized pass/fail + failure-phase hint, hides the
  reference solution). MVP can skip the in-rollout verifier tool and use the verifier only
  for *scoring*; the in-rollout dense-feedback tool is a clean upgrade.

### Mining failure modes (the #18a mandate)

"Score against a replay set that mines success AND failure modes" means the replay set is
**not just the passing trajectories.** Concretely:

- A `failure` / `user_pushback` / `off_track`-annotated trajectory is a **negative scoring
  row**: the variant *passes* that row only if it now *avoids* the recorded failure (e.g.
  the user pushed back last time → the variant's rollout must not re-trigger that pushback).
  This is the operationalization of "consider whether the user pushed back, not just usage."
- The **oracle reads the failure annotations as its primary grounding** for ADD lessons — a
  failure row isn't just a scoring row, it's the *reason* a lesson gets written.
- Balance the replay set: include both success rows (regression guard — don't break what
  worked) and failure rows (improvement target). A variant that fixes failures but breaks
  successes nets out flat on `PassRate` and won't be selected — which is correct.

---

## Versioning + revert

Safety = **versioning + revert** (Decision #20a, option A: `.versions/` archive, NOT
per-skill git — git nests badly with the project repo). Layout is fixed by
store-skill-loader.md; evolve is the writer.

### `.versions/` layout (loader-invisible, Decision #20a-i)

```
<skill-name>/
├── SKILL.md              # ACTIVE version — the ONLY thing the loader reads (SSoT identity)
├── (L2 files…)
└── .versions/            # dot-prefixed → globbed OUT of the loader scan (invisible)
    ├── v1/SKILL.md       # cold backups, one dir per archived version
    ├── v2/SKILL.md
    ├── v3/SKILL.md
    ├── lesson_bank.json  # evolve's per-skill working memory (persisted across runs)
    └── manifest.json     # {active: "v4", history: [{ver, ts, origin, parent}, …]}
```

- **Loader-invisible:** the dot-prefix means the loader's `iter_skill_dirs` never descends
  into `.versions/`, so archived versions never enter L0 and never trip the same-name
  conflict WARNING (the exact failure Decision #20a-i guards against). Verified in
  store-skill-loader.md §"Versions invisibility."
- **`origin` field** records provenance (`distill` | `evolve` | `human` | `revert`) — this
  is also how the write-permission policy is enforced: evolve may modify `origin ∈ {distill,
  evolve}` by default; `origin == human` is protected unless TUI `/config` allows it
  (Decision #20a). The candidate filter reads this (§Candidate filter).
- **`parent`** records lineage (which version this descended from) — free, and it's exactly
  the archive edge the DGM upgrade needs (§Upgrade path).
- **`lesson_bank.json`** lives here too so evolve resumes its KEEP set across runs rather
  than re-deriving from scratch each time.

### Revert mechanics

- `revert(skill, to_ver)` = `commit_version(skill, body=read(.versions/<to_ver>/SKILL.md),
  origin="revert", parent=<current>)` — a normal commit under the M4 lock, so it's atomic
  and race-free against evolve/distill. It creates a *new* version whose body equals an old
  one (forward-only history; we never delete archive entries — keep-ancestors is free and
  matches DGM).
- e2e #10 pass criterion: after merge, version +1; `revert` restores the old version and the
  old version still loads. Both are direct consequences of the layout above.

### Merge gate (Decision #20c)

evolve does **not** silently write the active skill. The selector's final survivor is
emitted as a proposal event (`evolve.variant_proposed`, payload = skill_name, diff,
pass-rate delta, replay evidence). The autonomy gate (default `assist` → confirm-required)
decides: human-confirm → `commit_version(origin="evolve")`; `auto` → auto-commit; `off` →
no-op (e2e #14). The confirmation itself becomes a `user_approval` annotation in the trace
store (closing the loop). evolve's code path stops at "propose"; the *write* happens on
confirmation.

---

## Candidate filter (reasonable-trace requirement)

Decision #20b: **having reasonable trace is a candidate FILTER, not an error.** A skill that
lacks enough trace to score a variant simply doesn't enter the evolve candidate set.

```python
def is_evolve_candidate(skill, cfg) -> bool:
    # 1. write-permission policy (Decision #20a)
    if skill.origin == "human" and not cfg.evolve.may_modify_human_skills:
        logger.debug(f"skip evolve: {skill.name} is human-authored and protected")
        return False
    # 2. reasonable-trace filter (Decision #20b) — the core gate
    rows = trace_store.replay_set(skill.name, min_outcome_quality="scored")
    if len(rows) < cfg.evolve.min_replay_rows:          # e.g. >= K rows
        logger.debug(f"skip evolve: {skill.name} has {len(rows)} replay rows "
                     f"(< {cfg.evolve.min_replay_rows})")
        return False
    # 3. there must be an improvement SIGNAL, not just usage (Decision #18a)
    if not any(r.has_failure_signal for r in rows):     # failure/pushback/off_track present
        logger.debug(f"skip evolve: {skill.name} has no failure/pushback signal — nothing to fix")
        return False
    return True
```

- **Silent-skip = `logger.debug`, NOT a raise** (Decision #20b, spec §7). A skill without
  trace is the *normal* "trigger condition not met" case — fail-loud here would be noise
  (signal-to-noise; CLAUDE.md "Fail-Loud is for swallowed real signals, not for normal
  not-triggered"). The DEBUG line is still *observable* under `-v`, so it's not a silent
  swallow — you can see *why* a skill wasn't evolved.
- **Fail-loud is reserved for true exceptions** *during* an evolve run that passed the
  filter: trace-store read failure, corrupt skill, model-call failure → raise (spec §7).
  The asymmetry is deliberate: "no candidate" is silent; "candidate broke" is loud.
- **Three gates, ordered cheap→expensive:** permission (free field read) → replay-row count
  (one store query) → failure-signal presence (already in the rows). The `skill_used`
  accumulation only *enqueues*; this filter is what actually admits a candidate to a run.
- **`min_replay_rows`** is the knob tying "reasonable trace" to a number. It's also the
  floor that makes `F_LCB` meaningful — too few rows and the confidence bound is vacuous.

---

## Upgrade path (DGM)

MVP is mutator–selector with a **single survivor carried forward** (greedy hill-climb with
parent-carry). The structural room left for the Darwin-Gödel Machine (Sakana, arXiv
2505.22954) upgrade:

| Aspect | MVP (mutator–selector) | DGM upgrade |
|---|---|---|
| Population memory | survivor + lesson bank only | **full archive** of all evaluated variants (keep-ancestors) |
| Parent selection | always the single survivor | **sample** a parent from the archive, prob. ∝ fitness × novelty |
| Diversity | none (greedy) | stepping-stones: low-fitness ancestors can seed later breakthroughs |
| Risk | premature convergence on a local optimum | open-ended exploration escapes local optima |

**What carries over for free:** the `.versions/` archive already keeps every version with
`parent` lineage edges and a `score` (the survivor's `SelectMetrics.pass_rate`). That *is* a
DGM archive — keep-ancestors is the default (we never delete versions). The MVP just doesn't
*sample* from it; it always picks the latest survivor.

**DGM parent-selection (the upgrade core):** DGM keeps an archive of all discovered agents
and grows it by sampling one agent and mutating it. **Every archived agent has non-zero
selection probability**; better-performing agents are likelier, and probability is *also*
modulated by how many children an agent already has (a novelty/diversity bonus that
down-weights over-explored nodes). Qualitatively (Sakana §3 / §A.2):

```
P(select agent a) ∝ sigmoid_scaled(performance_a) · novelty_bonus(a)
novelty_bonus(a) ≈ 1 / (1 + n_children_a)      # down-weight already-expanded nodes
```

(The paper's exact constants are in its appendix A.2; the shape — sigmoid-scaled fitness ×
inverse-children novelty, with a non-zero floor for every node — is what to replicate.) The
key DGM insight to preserve: **"archived solutions serve as stepping stones that yield
improvements much later than their original discovery"** — i.e. don't prune low-fitness
ancestors (which a pure hill-climber would), because they avoid premature convergence.

**Upgrade is additive, not a rewrite:** swap "carry the single survivor" for "sample a
parent from the version archive by the formula above," and let the mutator descend from the
sampled parent instead of always the latest. The lesson bank, oracle, selector (`SelectQ`,
`F_LCB`), replay harness, and M4 write path are unchanged. This is exactly the #18a "keep
the fitness kernel, defer the archive (YAGNI for MVP)" plan — and the archive is already on
disk, just not yet sampled from.

**Why defer (YAGNI):** archive sampling pays off at scale (DGM ran 80 generations to go
SWE-bench 20%→50%). For a handful of skills with modest trace, greedy mutator–selector with
parent-carry captures most of the lift at a fraction of the model-call budget. Add DGM
sampling when a skill plateaus and you have the compute to explore.

---

## Industry refs with URLs

- **Trace2Skill: Verifier-Guided Skill Evolution for Long-Context EDA Agents** (2605.21810)
  — *the* oracle–mutator–selector loop, `SelectQ`/`F_LCB`, KEEP/ADD/REMOVE lesson bank,
  PassRate-dominant selection, `verify_feedback` dense tool. https://arxiv.org/abs/2605.21810
  · HTML: https://arxiv.org/html/2605.21810
- **Trace2Skill: Distill Trajectory-Local Lessons into Transferable Agent Skills**
  (2603.25158) — the *distill* (S3) paper; do NOT use for evolve. https://arxiv.org/abs/2603.25158
- **Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents** (Sakana, arXiv
  2505.22954) — archive + fitness, keep-ancestors stepping-stones, parent sampling ∝
  performance × novelty; SWE-bench 20%→50%, Polyglot 14.2%→30.7%. The MVP upgrade path.
  https://arxiv.org/abs/2505.22954 · blog: https://sakana.ai/dgm/
- **GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning** (arXiv
  2507.19457, ICLR 2026 oral) — reflective text evolution, Pareto-frontier candidate
  sampling (prob ∝ coverage), `(score, feedback)` per rollout; +6–20% over GRPO at up to 35×
  fewer rollouts. Validates "reflect on traces in natural language → evolve the prompt
  artifact." https://arxiv.org/abs/2507.19457 · DSPy impl: https://dspy.ai/api/optimizers/GEPA/overview/
- **GEPA reference implementation** — https://github.com/gepa-ai/gepa
- **SkillOpt: Executive Strategy for Self-Evolving Agent Skills** (arXiv 2605.23904) —
  contemporary skill-evolution framing; adjacent reading. https://arxiv.org/abs/2605.23904

---

## Open questions

1. **Replay cost ceiling.** `K · R · N_replay · G` model calls per evolve run can be large.
   What are sane defaults? (Trace2Skill: small `K`, `R≈4`.) Should `R` adapt — start at 1,
   add repeats only when candidates tie on `PassRate`? (Cheaper, same ranking quality.)
2. **Replay-judge fidelity.** For non-re-runnable tasks the judge-only fallback estimates
   "would the variant have done better" from a recorded reference. How much does judge
   noise erode `SelectQ` ordering? Need a calibration check (do judge-only runs ever
   propose regressions that re-rollout would have caught?).
3. **Failure-row "pass" definition.** "Variant passes a failure row iff it avoids the
   recorded failure" — operationalizing this needs a per-row success predicate. Is it the
   verifier (re-rollout) or a judge comparing against the failure annotation? Schema-first:
   does the trace store need a per-row `failure_avoided` predicate, or does evolve compute
   it from `outcome`?
4. **Cross-process lock boundary.** MVP assumes single daemon (Decision #6). If a future
   multi-process deployment shares a skills dir, the asyncio lock is insufficient — when do
   we add the `flock`/lockfile layer, and does the manifest need a CAS version stamp?
5. **Lesson-bank growth / decay.** KEEP accumulates across generations. Does the bank need a
   size cap or a decay (REMOVE stale KEEP rules that no longer ground in any current trace)?
   Mirrors dream's memory-decay concern but for the per-skill lesson bank.
6. **Multi-skill interaction.** evolving skill A may change how skill B's traces look
   (skills compose in one session). Replay sets are scoped per-skill (`replay_set(name)`) —
   is that scoping enough, or do co-occurring skills need joint evaluation? (Likely YAGNI
   for MVP; flag for the eval-harness module #17.)
7. **`B(S)` regression baseline drift.** `B(S)=1` floors a variant that regresses "vs
   parent." As the active skill improves, the baseline moves. Is the baseline always the
   *current active* skill, or the gen-0 parent of this run? (Recommend: current active, so a
   proposal can never ship a net regression — ties to the acceptance gate.)
