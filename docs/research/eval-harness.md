# Module: eval-harness (the "10th star") — self-measuring experiment bench

Module: `agentkit/eval/` (a Ring-2-adjacent **consumer**, not a subsystem — it drives the
kernel from outside via the public SDK, exactly like the CLI does)
Decisions: #31 (CEO 10th-star = self-measuring eval harness, kernel-only vs +subsystem,
success/cost delta), #13 (single `AgentConfig` SSoT → an experiment is just two configs),
#17 (trace store = the eval record), #18a/#20b (evolve already has a replay+LCB scorer —
**reuse it, do not re-invent**), #28 (provider `Usage` already carries token cost),
#10/#14 (everything swappable/toggleable via config). Eng-review tie-in: none of H1–M7
(this module is read-mostly), but it is the thing that would *detect* a regression any of
them introduce.

Primary external refs: **OpenAI simple-evals** (~100 lines/eval), **mini-SWE-agent**
(~100-line agent >74% SWE-bench Verified), **Inspect AI** (task = dataset+solver+scorer —
the naming we borrow), **SWE-bench harness** (fail_to_pass/pass_to_pass resolved-criterion),
**"Beyond pass@1" reliability framework** (variance across repeats is the real signal).

> Thesis check. Alfred's whole stated goal (B) is "harness > model — does subsystem X
> actually help?". The spec *asserts* this in 30 decisions and *measures* it in zero. This
> module is the one that turns an assertion into a number. It is the 10th star precisely
> because without it Alfred is "a framework with swappable parts"; with it, Alfred is "an
> experiment bench that emits findings."

---

## Module scope

One sentence: *run a fixed task set under config-A and config-B, score each rollout,
aggregate with repeats, and print "subsystem X gave +N% success at +M% cost" with a
confidence interval.*

**In scope (this module owns):**
- `Experiment` = (task_set, [arm_A, arm_B, ...], scorer, repeats) — a pydantic spec, the
  eval-side SSoT, loadable from YAML like everything else (#13).
- The **runner**: for each (arm × task × repeat) build an `Agent` from that arm's
  `AgentConfig`, run it to completion, capture the final answer + the trace + `Usage`.
- The **scorer interface** (Inspect-style): `(task, rollout) -> Score{value: float in [0,1],
  passed: bool}`. Ships 3 built-ins (exact/contains, predicate, llm-judge).
- **Aggregation + the delta report** — per-arm pass-rate, mean cost, and the A↔B delta with
  a paired confidence interval (reuse evolve's `F_LCB`).
- The **findings artifact** — one machine-readable `findings.json` + one human `report.md`.

**Out of scope (delegated / deliberately not built):**
- Re-rollout / replay machinery, `F_LCB`, repeats-variance math → **already exists in
  evolve** (subsystem-evolve.md §Selector). The eval harness *imports the same scorer
  helpers*; it does not own a second copy (DRY — CLAUDE.md).
- Trace storage / annotation capture → trace store (Ring 2). Eval **reads** traces; it is
  not a new store.
- Building agents / toggling subsystems → that is `AgentConfig` (#13) + the registries.
  The eval harness never knows what a "subsystem" is; it only knows "config A" vs "config B".
- A web dashboard / leaderboard → out of MVP (the SSE outlet + a markdown report suffice).
- Heavy task corpora (full SWE-bench) → MVP ships a tiny curated set; SWE-bench-Lite is an
  opt-in adapter, not a dependency.

**Why a consumer, not a subsystem.** Ring-3 subsystems run *inside* one agent's loop via
hooks. The eval harness runs *many agents from the outside* and compares them — it sits
where the CLI sits (a consumer of the public SDK). Making it a subsystem would invert the
ring dependency (it would need to spawn loops, which Ring-3 must not do).

---

## Minimal Harness, Not Toy Evaluation

The framework should stay small, but the measurement must be real. "Minimal" means the
core abstractions are few (`runner` + `scorer` ABC + `aggregate`), not that the result is
a one-off mock demo. A trustworthy eval run still needs a curated task set, repeats,
paired confidence intervals, real `trace_id`s, and measured provider `Usage`.

```python
# agentkit/eval/types.py  — the eval-side SSoT (pydantic, like AgentConfig)
class Task(BaseModel):
    id: str
    prompt: str                       # what the user would type
    target: str | None = None         # reference answer / predicate input (scorer decides)
    setup: dict | None = None         # optional: files to drop, cwd, fixtures
    metadata: dict = {}

class Score(BaseModel):
    value: float                      # [0,1] dense; 1.0/0.0 for binary
    passed: bool                      # the resolved-criterion (SWE-bench style)
    detail: str = ""                  # judge rationale / mismatch, for the report

class Arm(BaseModel):
    name: str                         # "kernel-only" | "kernel+memory"
    config: AgentConfig               # <-- the entire A/B mechanism lives here (#13)

class Experiment(BaseModel):
    name: str
    arms: list[Arm]                   # ≥2; arm[0] is the baseline
    task_set: list[Task]              # or a loader ref: {type: jsonl, path: ...}
    scorer: ScorerSpec                # {type: contains|predicate|llm_judge, params}
    repeats: int = 5                  # rollouts per (arm,task) — drives the CI (#variance)
    seed: int = 0
```

```python
# agentkit/eval/runner.py  — ~40 lines, the heart
async def run_experiment(exp: Experiment, providers) -> list[Rollout]:
    scorer = build_scorer(exp.scorer, providers)        # registry-style, like models
    rollouts = []
    for arm in exp.arms:
        for task in exp.task_set:
            for r in range(exp.repeats):
                agent = Agent(arm.config)               # fresh agent = fresh session+trace
                result = await agent.run(task.prompt, setup=task.setup)  # public SDK call
                score  = await scorer(task, result)     # (task, rollout) -> Score
                rollouts.append(Rollout(
                    arm=arm.name, task=task.id, repeat=r,
                    score=score,
                    usage=result.usage,                 # provider Usage (#28) = cost SSoT
                    trace_id=result.trace_id,           # the eval record IS the trace (#17)
                ))
    return rollouts
```

```python
# agentkit/eval/scorer.py  — the ABC + 3 built-ins (~30 lines total)
class Scorer(Protocol):
    async def __call__(self, task: Task, rollout: AgentResult) -> Score: ...

# contains/exact : passed = task.target in rollout.final_text         (simple-evals style)
# predicate      : passed = user-supplied callable / verifier command  (SWE-bench style)
# llm_judge      : passed = judge_provider.complete(rubric).verdict    (Inspect style)
```

**Design rules that keep it minimal and trustworthy:**

1. **Fresh agent per rollout.** Each rollout constructs a new `Agent(arm.config)` → fresh
   session + fresh trace. No cross-rollout state leak (the #1 way eval numbers become
   untrustworthy). This is cheap because the kernel is tiny.
2. **No new abstractions.** `Arm.config` is the *same* `AgentConfig` the CLI loads. The
   scorer registry mirrors the `models` registry pattern (#8). Nothing here is eval-only
   machinery the rest of Alfred doesn't already have.
3. **Cost is read, never estimated.** `result.usage` is the provider `Usage` (#28:
   `prompt_tokens/completion_tokens/total_tokens/cached_tokens`). Cost delta = token delta,
   measured, not guessed. Optionally multiply by a per-model `$/Mtok` table for dollars.
4. **Repeats are mandatory, default 5.** A single rollout per arm is the classic untrust
   trap — frontier models swing 10–20pts run-to-run on the same harness ("Beyond pass@1").
   The delta must come with a CI or it is noise.
5. **Fail-loud on arm parity violations.** If arm A and arm B differ in anything *other*
   than the toggled subsystem (different model, different task set), the experiment is
   confounded → crash at load (`Experiment.validate()` diffs the two configs and asserts
   exactly one axis changed; see §A/B). This is the eval-harness analogue of `extra=forbid`.
6. **Mock-backed runs are integration tests, not eval proof.** Unit/integration tests may
   use `MockProvider` to exercise the harness mechanics. A harness "works" claim requires a
   live profile: `alfred eval run` over a small curated task set using real providers,
   repeats >1, trace ids written, usage/cost populated, and CI output in the report.

---

## A/B mechanism

The lever Alfred already built: **#13 makes an experiment = two `AgentConfig`s.** Because
every subsystem is declared in config and toggleable (`{type, params}` nesting, `skill_filter
disable`, `autonomy`, presence/absence of a Ring-3 entry), the A/B mechanism needs *zero new
toggling code*. The experiment author writes two configs that differ on exactly one axis.

**Express an experiment as two configs + a task set + a scorer:**

```yaml
# experiments/memory-helps.yaml
name: does-memory-help
repeats: 5
task_set: {type: jsonl, path: ./tasks/recall-20.jsonl}
scorer:   {type: llm_judge, params: {judge: {type: litellm, params: {model: claude-haiku-4-5}}}}
arms:
  - name: kernel-only
    config:
      model: {type: litellm, params: {model: claude-opus-4-8, env_key: ANTHROPIC_API_KEY}}
      memory: {type: none}                     # <-- the ONLY difference
  - name: kernel+memory
    config:
      model: {type: litellm, params: {model: claude-opus-4-8, env_key: ANTHROPIC_API_KEY}}
      memory: {type: files, params: {root: ./mem}}   # <-- toggled on
```

**The single-axis invariant (parity guard).** `Experiment.validate()` deep-diffs
`arms[i].config` against `arms[0].config` and asserts the differing keys are a subset of a
declared `varies:` list (or exactly one path if `varies` omitted). Same model, same task
set, same scorer across arms — only the subsystem flips. Anything else → fail-loud. This is
what makes the delta attributable to subsystem X rather than to a confound.

**What "kernel-only" means concretely.** The baseline arm is the Tier-0 config (#31):
kernel + session + skill-loader + 1 provider, every Ring-3 entry absent or `{type: none}`,
`autonomy: off` (no auto-loops perturbing the run). Each experiment flips on exactly one of:
memory / distill / dream / evolve / goal / fusion / handoff / mcp / a skill set. That yields
a one-line finding per subsystem — which is the entire deliverable the CEO asked for.

**Composability (free).** Because arms are a *list*, not a pair, you can A/B/C: kernel-only
vs +memory vs +memory+goal. The aggregator compares each arm to `arms[0]`.

**Reuse over re-roll.** The "run a config against a task set and score it" loop is the same
shape evolve already runs to score skill variants (subsystem-evolve.md §"ROLLOUT + SCORE
each candidate over the replay set, R repeats each"). Evolve scores *variants of one skill*;
eval scores *whole configs*. **Both call the same `score_rollouts()` + `F_LCB` helpers** in
`agentkit/eval/aggregate.py` — evolve becomes the first internal consumer of the eval
module's scorer, not a parallel implementation (DRY; collapses two scorers into one).

---

## Task set + scoring

### Where tasks come from (build vs reuse)

Bias: **build a tiny custom set first; adapt an external slice as opt-in.**

| Source | Use it for | Build cost | MVP? |
|---|---|---|---|
| **Custom curated set (10–30 tasks)** | the subsystem-targeted questions ("does memory help recall?", "does goal finish multi-turn?") | author 1 JSONL file | **yes — primary** |
| **SWE-bench-Lite slice (5–20 instances)** | the headline "harness>model" coding number; reuses the existing Docker resolved-criterion | a thin `predicate` scorer that shells `fail_to_pass`/`pass_to_pass` | opt-in adapter |
| **Reuse evolve replay sets** (`trace_store.replay_set`) | regression eval on real historical traces — "did this change break what used to work?" | already exists (#17/#18a) | free, recommended |

The custom set is primary because each Alfred subsystem has a *specific* claim to test, and
generic benchmarks (SWE-bench) don't isolate "does dream's memory housekeeping help". One
task family per subsystem:
- memory → multi-session recall tasks (matches e2e #3).
- goal → multi-turn tasks that can't finish in one turn (matches e2e #8).
- skills → tasks that a dropped-in skill should solve (matches e2e #4).
- fusion → tasks with a checkable single answer where vote should beat single (e2e #6).

> **The task set is just the e2e contract rows, parameterized.** §10 already enumerates 17
> user-observable paths. The eval task set is those same scenarios at N≥10 instances each
> with a scorer attached. Eval = "e2e contract, run at scale, scored, with an A/B arm."

### Success criteria (the resolved-criterion)

Borrow SWE-bench's discipline: **`passed` is binary and objective per task**, `value` is the
dense signal for tie-breaking. Three scorer types cover the spec's task families:
- **contains/exact** (simple-evals): `target` string present in the final answer. For
  factual recall (memory) and tool-content tasks (e2e #1: "prints the real content").
- **predicate/verifier** (SWE-bench): a callable or shell command returns 0/1. For code
  tasks (`fail_to_pass` passes) and goal tasks (goal-predicate met).
- **llm_judge** (Inspect): a judge provider scores against a rubric → verdict. For
  open-ended answers. The judge is *itself* an injected `ModelProvider` (#11), so it reuses
  the provider layer and `env_key` secret hygiene — no new dependency.

### Cost tracking

Free — `Usage` already flows through the provider boundary (#28). Per rollout record
`prompt_tokens + completion_tokens` (and `cached_tokens` separately, because a subsystem
that bloats the frozen prefix shows up as a cache-economics regression, tying to #29).
Cost delta is reported in **both** tokens (always available) and dollars (if a `$/Mtok`
table is configured). "X gave +N% success at +M% cost" — M comes straight from summed
`total_tokens` per arm.

### Variance / repeats (reuse evolve's LCB)

This is the trustworthiness core and it is **already designed** — do not rebuild it.

- Run `repeats` rollouts per (arm, task) (default 5).
- Per arm: `pass_rate = mean(passed)`; `F̄_progress = mean(value)`;
  `F_LCB = max(0, F̄_progress − 1.96·σ/√(repeats))` — evolve's exact formula
  (subsystem-evolve.md §Selector). A lucky single pass (high σ) is penalized; consistent
  passing (low σ) survives.
- **The A/B delta uses a paired test**, because the SAME tasks run under both arms (paired
  design — far more power than unpaired). Per task, compute
  `Δ_task = pass_rate_B(task) − pass_rate_A(task)`; report `mean(Δ_task)` with a paired
  95% CI (bootstrap over tasks, or McNemar on the binary table). **Headline claim "X helps"
  is gated on the CI excluding 0** — otherwise the finding is reported as "no measurable
  effect (Δ within noise)", honoring fail-loud over a flattering-but-fake number.
- Budget honesty: `arms × tasks × repeats` rollouts. 2 × 20 × 5 = 200 real LLM calls per
  experiment — affordable for the curated set; this is why the MVP task set is *tiny* and
  SWE-bench is opt-in.

---

## Ties to trace + e2e

**Traces ARE the eval record (#17).** Every rollout already writes a full annotated trace
(loop writes at `pre/post_tool`/`turn_end`). So a `Rollout` row does not duplicate the
trajectory — it stores `trace_id` and the derived `Score` + `Usage`. The eval result is a
thin *index over traces*:

- **No new storage.** `findings.json` references `trace_id`s; the bodies live in the trace
  store (SSoT — the eval harness adds a derived view, reconstructible from traces + the
  `Experiment` spec, satisfying CLAUDE.md "all derived views reconstructible from SSoT").
- **The scorer's verdict is an annotation.** A `Score` written back as a `user_approval`/
  `score` annotation on the trace (trace store already has the `annotations` table, kind +
  value + confidence). This means **eval results feed evolve for free**: a config that an
  experiment proved good becomes scored replay material. The loop closes — eval measures,
  evolve optimizes, both read the same annotated traces.
- **Re-rollout symmetry.** Eval's `run_experiment` and evolve's variant-rollout are the same
  operation at different granularity (whole config vs one skill). Both produce traces, both
  score with `F_LCB`. One `score_rollouts()` helper, two callers.

**Eval is the e2e contract, scaled and scored.** The §10 e2e rows are the *qualitative* "does
the path work once" gate (wayne-verify flips ⬜→✅). The eval harness is the *quantitative*
"how often, at what cost, vs baseline" measurement. They share scenarios:
- e2e #1 (tool prints real file content) → an eval task family scored by `contains`.
- e2e #3 (recall) → the memory experiment's task set.
- e2e #6 (fusion votes cross-vendor) → the fusion experiment, scored answer-correctness,
  arm A = single model, arm B = fusion.
- e2e #8 (goal self-continues) → goal experiment, predicate scorer on goal-met.

A new declared e2e row covers the harness itself (number it after the carried #1-#29
contract rows, e.g. #30, so it does not collide with L9 negative-path rows):

| # | User path | Process | Data | Entrypoint | Observable (pass = ?) | Status |
|---|---|---|---|---|---|---|
| 30 | Dev runs an A/B experiment (kernel-only vs +subsystem) over a task set; gets a delta report | `alfred eval run experiments/X.yaml` | tiny task set + 2-arm config | `alfred` CLI | `report.md` prints per-arm pass-rate + cost + the A↔B delta with a CI; each rollout links a real `trace_id`; both arms ran the SAME tasks (parity guard logged) | ⬜ |

`E2E: none` clarifier to add: *the scorer ABC / aggregation math are internal; observable
only via the eval row's report.*

**Test naming contract:** any eval test under `tests/e2e/` must use real LLM calls. Mock
tasks belong under `tests/integration/` and can only prove harness plumbing. The live eval
smoke should be tiny (for cost) but real: at least two arms, repeats >1, one objective
scorer, trace ids in `rollouts.jsonl`, usage/cost fields populated, and a markdown report
with paired CI.

---

## Findings report

Two artifacts per experiment run. The JSON is the SSoT-index; the markdown is the human
deliverable (the CEO's "produces findings").

**`findings.json`** (machine-readable, references traces):
```json
{
  "experiment": "does-memory-help",
  "baseline": "kernel-only",
  "repeats": 5,
  "arms": [
    {"name": "kernel-only",   "pass_rate": 0.55, "f_lcb": 0.49, "mean_tokens": 4200},
    {"name": "kernel+memory", "pass_rate": 0.78, "f_lcb": 0.71, "mean_tokens": 5100}
  ],
  "deltas": [
    {"arm": "kernel+memory", "vs": "kernel-only",
     "success_delta_pct": 23.0, "success_ci95": [11.0, 35.0], "significant": true,
     "cost_delta_pct": 21.4, "cost_delta_tokens": 900}
  ],
  "rollouts_ref": "rollouts.jsonl",       // each row: {arm,task,repeat,score,usage,trace_id}
  "tasks": 20
}
```

**`report.md`** — the headline the bench exists to produce:
```
# does-memory-help  (20 tasks × 5 repeats, claude-opus-4-8)

kernel+memory gave +23.0% success (95% CI [+11, +35], significant)
              at  +21.4% cost (+900 tok/task).

| arm           | pass-rate |  F_LCB | mean tokens |
| kernel-only   |     55%   |  0.49  |    4,200    |
| kernel+memory |     78%   |  0.71  |    5,100    |

Verdict: memory HELPS — effect exceeds noise. Cost-justified if +1pt success
worth ~39 tok. Drill-down: traces for the 4 tasks memory flipped → rollouts.jsonl.
```

When the CI includes 0 the verdict line reads **"no measurable effect (Δ within run-to-run
noise) — do not claim X helps"** (fail-loud against flattering noise). That negative finding
is itself valuable: it tells the experimenter the subsystem isn't earning its complexity
(Delete>Add).

---

## Industry refs with URLs

- **OpenAI simple-evals / Evals** — minimal, modular eval harness; keep core types generic,
  ~one file per eval. (Note: hosted Evals platform sunsets 2026-11; the *pattern* — tiny
  per-eval files — is what we borrow.)
  https://github.com/openai/evals · https://developers.openai.com/learn/evals
- **OpenAI "agent improvement loop" (traces → evals → harness changes)** — the exact
  measure-then-improve flywheel Alfred's trace→eval→evolve chain mirrors.
  https://developers.openai.com/cookbook/examples/agents_sdk/agent_improvement_loop
- **mini-SWE-agent** — ~100-line agent, bash-only, >74% SWE-bench Verified. The "smallest
  thing that works" north star.
  https://github.com/SWE-agent/mini-swe-agent
- **Inspect AI** — task = dataset + solver + scorer; the vocabulary and the scorer taxonomy
  (extract / similarity / model-graded) we adopt.
  https://inspect.aisi.org.uk/ · https://inspect.aisi.org.uk/scorers.html · https://inspect.aisi.org.uk/tasks.html
- **SWE-bench harness** — resolved = all `fail_to_pass` pass AND all `pass_to_pass` still
  pass; Dockerized run_evaluation. Source of the binary resolved-criterion.
  https://www.swebench.com/SWE-bench/reference/harness/ · https://github.com/princeton-nlp/SWE-bench/blob/main/swebench/harness/run_evaluation.py
- **"Beyond pass@1: A Reliability Science Framework for Long-Horizon LLM Agents"** —
  variance across repeats is the real signal; single-run scores are untrustworthy.
  https://arxiv.org/html/2603.29231
- **Applying Statistics to LLM Evaluations (Wolfe)** — paired tests, CIs, "ranking at the
  top is within noise." Justifies the paired-CI gate on the delta.
  https://cameronrwolfe.substack.com/p/stats-llm-evals
- **NIST AI 800-3 — statistical models for AI evaluation** — GLMM variance decomposition
  (between-question vs within-question); rigor backing for the repeats design.
  https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.800-3.pdf
- **EleutherAI lm-evaluation-harness** — the "many tasks, one runner" registry pattern at
  scale; reference for the task-loader shape (we stay far smaller).
  https://github.com/EleutherAI/lm-evaluation-harness
- **Survey: Evaluation and Benchmarking of LLM Agents (2507.21504)** — taxonomy of agent
  eval; situates trace-based + A/B harness evaluation.
  https://arxiv.org/html/2507.21504v1

---

## Open questions

1. **Scorer for non-verifiable tasks.** contains/predicate are objective; llm-judge has its
   own variance and bias. For open-ended subsystems (dream's memory tidiness) is there *any*
   objective proxy, or is llm-judge-with-repeats the honest ceiling? (Lean: judge + report
   the judge's own variance.)
2. **Sequential-dependency tasks.** Fresh-agent-per-rollout breaks tasks that need built-up
   memory across turns. Memory/goal experiments need a *session-scoped* rollout (N turns,
   one agent) as a second rollout mode. Add `Task.turns: list[str]` for multi-turn tasks?
3. **SWE-bench Docker as a dependency.** The predicate scorer for SWE-bench-Lite needs the
   Docker harness. Keep it a fully optional extra (`pip install alfred[swebench]`) so the
   core eval module stays dependency-light — confirm the adapter never imports into core.
4. **Repeat budget vs sequential testing.** Evolve already asks (subsystem-evolve.md OQ): add
   repeats only when arms *tie*? A sequential/adaptive scheme (run 2, escalate to 5 only if
   CI straddles 0) would cut LLM spend ~2× at equal ranking quality. Shared with evolve.
5. **Cost model for fusion arms.** Fusion fires N providers per call; `total_tokens` summed
   across workers captures it, but the `$/Mtok` table must be per-worker-model. Confirm the
   composite `Usage` from a fusion provider aggregates workers (provider-layer dependency).
6. **Where do experiments live + run headless.** `./experiments/*.yaml` loaded like configs;
   should the cron daemon (#6) be able to schedule a nightly regression experiment writing
   `findings.json` to a results dir? (Natural fit; defer to keep MVP a CLI command.)
7. **Baseline drift.** "kernel-only" baseline changes as the kernel evolves. Should
   `findings.json` pin the Alfred git SHA + model id so historical deltas stay comparable?
   (Lean: yes — record SHA + model + provider in every findings header.)
