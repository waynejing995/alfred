# Subsystem Research: fusion (model fusion as composite provider)

Module: Ring-3 — fusion. Flagship feature.
Decisions: #10 (composite ModelProvider), #11 (pluggable aggregator), #23 (fusion ≠ handoff
split), #28 (LiteLLM behind ABC). Eng-review gap: **M6** (worker timeout / quorum /
partial-failure / judge-failure). E2E: contract **#6** (forced cross-vendor ensemble vote).
Sibling docs this aligns with: `provider-layer.md` (owns `ModelProvider` ABC + pydantic
types), `subsystem-handoff.md` (puppeteer moved there), `kernel-loop-budget.md`.

---

## Module scope

**In scope.** fusion is a Ring-3 plugin that is *disguised as a provider*. It implements the
exact `ModelProvider` ABC from `provider-layer.md` and registers into the `models` registry
(#8). The loop calls `provider.complete(messages, tools, tool_choice)` and gets one
`ModelResponse` — it cannot tell whether it is talking to a `LiteLLMProvider` or a
`FusionProvider`. Internally fusion fans out to **N injected sub-providers** in parallel and
aggregates their `ModelResponse`s into one.

Two shapes ship in MVP (Decision #10 table):

| Shape | Composition | Aggregator |
|---|---|---|
| ensemble / vote | N worker providers, same messages | code rule (majority / rank / concat) OR LLM-judge |
| router / dispatch | 1 router provider picks → forward to 1 worker | forward (no aggregation) |

**Out of scope (moved by Decision #23):**
- **puppeteer / orchestrate** — relocated to `handoff` (multi-AGENT, independent
  context/loop/tools). Puppeteer commands *agents*, not *models on the same messages*; the
  provider abstraction cannot hold independent-context collaboration. fusion is strictly
  "N models, **same** message list, **one** call boundary → one aggregated response."
- Aggregator is **NOT a 6th registry** (Decision #11) — it is a constructor param of
  `FusionProvider`. Workers + judge are **injected at construction** (config recursion does
  the wiring), so the whole chain depends only on `ModelProvider`, never on the loop. This is
  what prevents a loop→fusion→loop cycle.

**Hard invariants:**
1. fusion depends only on `ModelProvider` + Alfred pydantic types. It never imports the loop,
   the event-bus, or litellm.
2. The single-provider contract is preserved bit-for-bit: same signature, same
   `ModelResponse` out, same exception semantics (raise on hard failure).
3. Cross-vendor (Anthropic + OpenAI/Azure) is a first-class test target (e2e #6), not an
   afterthought — it falls out for free because every worker is a `LiteLLMProvider` on a
   different model.

---

## Composite provider pattern

### Concrete sketch

```python
# agentkit/subsystems/fusion/provider.py
import asyncio
from agentkit.kernel.providers.base import ModelProvider
from agentkit.kernel.providers.types import Message, ToolDef, ModelResponse, Usage
from .aggregators import Aggregator
from .policy import FusionPolicy        # timeout / quorum / fallback — see M6 section

class FusionProvider(ModelProvider):
    """A ModelProvider that fans out to N workers and aggregates. Decision #10/#11."""

    def __init__(
        self,
        workers: list[ModelProvider],          # injected (config recursion wires these)
        aggregator: Aggregator,                # code-rule OR llm-judge instance (NOT a registry)
        policy: FusionPolicy,                  # M6 knobs: per_worker_timeout_s, quorum, fallbacks
    ):
        if not workers:
            raise ValueError("fusion requires >=1 worker provider")   # fail-loud at construction
        self._workers = workers
        self._aggregator = aggregator
        self._policy = policy

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        tool_choice: str | None = None,
        **params: object,
    ) -> ModelResponse:
        # 1. fan out — every worker sees the SAME (messages, tools, tool_choice)
        results = await self._fan_out(messages, tools, tool_choice, **params)
        # results: list[WorkerOutcome] aligned with self._workers (ok | error | timeout)

        # 2. quorum gate (M6) — raise if too few real responses
        responses = self._policy.enforce_quorum(results)   # -> list[ModelResponse], or raises

        # 3. aggregate — code rule or llm-judge (judge calls via ITS injected provider)
        agg = await self._aggregator.aggregate(
            responses=responses, messages=messages, tools=tools,
        )

        # 4. return ONE ModelResponse — usage is summed across workers (cost honesty)
        agg.usage = _sum_usage(r.usage for r in responses) | agg.judge_usage()
        return agg.response                                  # plain ModelResponse to the loop
```

### How it preserves the single-provider contract

- **Signature identity.** `complete(messages, tools, tool_choice, **params)` is copied verbatim
  from the ABC in `provider-layer.md`. The loop binds to the ABC, not the concrete class.
- **Return identity.** Output is a plain `ModelResponse(message=..., usage=..., finish_reason=...)`.
  The loop's parse/dispatch path is untouched — it reads `response.message.tool_calls` exactly
  as it would for a single model. fusion's complexity is fully encapsulated below the boundary.
- **Exception identity.** A *hard* fusion failure (quorum not met, all workers down) **raises**,
  same as a single provider raising on a 500 — the loop's existing model-call error handling
  (spec §7 fail-loud) catches it. fusion does NOT silently return a degraded empty response.
- **Usage honesty.** `usage` is the **sum** of all worker usages plus judge usage, so the
  iteration budget / cost accounting (#kernel:loop-budget) sees the true cost of fusion. This is
  load-bearing: an ensemble of 3 is ~3× the spend; hiding that would be silent degradation.
- **`stream()`** — for MVP, `FusionProvider` implements `complete()` only and, when the loop
  requests streaming, falls back to "buffer then emit one final delta" (you cannot
  token-stream a vote you haven't tallied yet). Document this explicitly; do not fake
  per-token streaming through an aggregator. (Router shape *can* stream — it forwards one
  worker — but MVP keeps a single code path: aggregate-then-emit.)

### Config wiring (Decision #13 recursion, verbatim from spec §6.2)

```yaml
model:
  type: fusion
  params:
    strategy: ensemble
    workers:
      - {type: litellm, params: {model: claude-opus-4-8, env_key: ANTHROPIC_API_KEY}}
      - {type: litellm, params: {model: gpt-5.5, env_key: AZURE_OPENAI_API_KEY}}
    aggregator:
      type: llm_judge
      params: {judge: {type: litellm, params: {model: claude-haiku-4-5}}}
    policy:                                  # M6 — see Gap answers
      per_worker_timeout_s: 30
      quorum: 1                              # min real responders
      judge_failure: fallback_code          # llm_judge fails -> code rule
      code_fallback_rule: concat
```

The config loader builds providers **bottom-up**: each `{type, params}` resolves via the
`models` registry; `fusion`'s factory receives already-constructed worker instances and a
constructed aggregator (whose `judge` is itself an already-constructed provider). This is the
injection of Decision #11 expressed as config recursion — no special-casing in the loader.

---

## Strategies + aggregators

### Strategy 1 — ensemble / vote (N workers, parallel)

Fan out the identical request to all N workers via `asyncio.gather`, then aggregate. This is
the **Mixture-of-Agents "proposers + aggregator"** pattern (Wang et al. 2024; MoA single-layer
= proposers→aggregator). MVP ships **single-layer** MoA; multi-layer (proposers feed a 2nd
proposer round) is a documented future extension, not built.

### Strategy 2 — router / dispatch (1 router + forward)

A cheap/fast router provider is asked (with a constrained prompt or `tool_choice`) which worker
should handle the request; fusion then forwards to that one worker and returns its response
unchanged. Aggregator = `forward` (identity). This is **Symbolic-MoE / skill-based routing**
(arxiv 2503.05641) reduced to MVP: one routing decision, one forward. Cost ≈ 1 small call +
1 real call (much cheaper than ensemble). No quorum needed (single worker) but per-worker
timeout still applies, and a router-failure fallback = "route to worker[0]".

### Aggregators (constructor param, pluggable — Decision #11)

```python
class Aggregator(ABC):
    @abstractmethod
    async def aggregate(self, responses, messages, tools) -> AggResult: ...
```

**A. Code rules (pure, no model call):**

| Rule | Behavior | Best for |
|---|---|---|
| `majority` | exact/normalized string vote on final text; ties → first or by worker priority | short factual answers ("1991") |
| `rank` | Borda / reciprocal-rank if workers emit ranked candidates; else priority order | when workers self-rank |
| `concat` | join all worker outputs with provenance headers (`## claude-opus-4-8 said:`) | synthesis-friendly downstream; safest no-op fallback |
| `semantic_majority` | cluster by embedding similarity, pick centroid of largest cluster | open-ended text where exact-match vote fails |

Research backing: self-consistency = majority vote / empirical mode (emergentmind);
**semantic** voting clusters by rationale similarity rather than exact string and yields gains
on open-ended tasks (arxiv 2509.23067); ranked voting (Borda/IRV/RRV) improves robustness
(ACL 2025 findings 744). `majority` and `concat` are the MVP defaults; `semantic_majority`
needs an embedding model (reuse memory's embedder) — ship as opt-in.

**B. LLM-judge (calls a model via the INJECTED judge provider):**

```python
class LLMJudgeAggregator(Aggregator):
    def __init__(self, judge: ModelProvider, rubric: str | None = None):
        self._judge = judge          # injected — depends only on ModelProvider (Decision #11)
    async def aggregate(self, responses, messages, tools):
        prompt = build_judge_prompt(responses, original=messages, rubric=self._rubric)
        verdict = await self._judge.complete(prompt, tool_choice="none")
        return parse_verdict(verdict, responses)   # pick-best OR synthesize
```

Judge best practices applied (from 2026 LLM-as-judge research):
- **Cross-family judging counters familial bias** — judges over-rate their own family
  (orq.ai; emergentmind). Recommend judge be a *different* family than the dominant workers,
  or a panel. e2e #6 (Anthropic+OpenAI workers) + a Haiku judge is already cross-checked.
- **Order randomization** — shuffle the worker-response order in the judge prompt to defeat
  position bias (agenta.ai). fusion shuffles before building the judge prompt.
- **Explicit rubric** — pass a rubric string; default rubric = "select the response that is
  most correct and complete; if combining, preserve all factual claims."
- **Panel > single judge when reliability matters** — "diverse imperfect judges outperform
  correlated perfect ones" (orq.ai LLM-juries). Panel-of-judges is a documented extension;
  MVP ships single-judge with cross-family guidance.

**Modes:** judge can `pick-best` (return one worker's response verbatim — preserves its
tool_calls intact, important for §tool-call) or `synthesize` (write a new answer — **text-only;
a synthesized answer with fabricated tool_calls is unsafe**, see tool-call section).

---

## Gap answers (M6)

This is the Eng-review gap: *worker timeout / quorum / partial-failure / judge-failure.*
Two real hazards: **(a) unbounded await** — `asyncio.gather` waits for the slowest worker, so
one hung vendor hangs the whole turn; **(b) silent N-1 voting** — dropping a failed worker
without policy is exactly the silent-degradation CLAUDE.md forbids. Design: an explicit
`FusionPolicy` that makes every degradation a *declared* choice.

### Per-worker timeout (kills hazard a)

Wrap each worker in `asyncio.wait_for` **inside the gather**, with `return_exceptions=True` so
one failure/timeout never cancels siblings (Python asyncio docs; superfastpython gather-timeout):

```python
async def _fan_out(self, messages, tools, tool_choice, **params):
    async def run(w: ModelProvider):
        try:
            r = await asyncio.wait_for(
                w.complete(messages, tools, tool_choice, **params),
                timeout=self._policy.per_worker_timeout_s,
            )
            return WorkerOutcome(worker=w, response=r)
        except asyncio.TimeoutError:
            return WorkerOutcome(worker=w, error="timeout")      # captured, not raised
        except Exception as e:                                   # 500/auth/rate-limit
            return WorkerOutcome(worker=w, error=repr(e))
    return await asyncio.gather(*(run(w) for w in self._workers))  # return_exceptions not needed:
                                                                   # each run() never raises
```

Notes:
- **`asyncio.wait_for` actively cancels** the timed-out coroutine, so a hung vendor's HTTP call
  is torn down (no leaked task). This is the key difference vs a single global
  `gather` timeout, which would leave slow tasks running.
- Each `run()` swallows its own exception **into a typed outcome** (not `except: pass`) — the
  outcome carries the error string for the trace + a WARNING log. The signal is captured, not
  lost (Fail-Loud compliant: visible, logged, counted).
- Python 3.11+ `asyncio.TaskGroup` is the structured-concurrency alternative, but TaskGroup
  *cancels all siblings on first error* — wrong for ensemble (we want partial success). So
  `gather` over individually-guarded tasks is the correct primitive here, not TaskGroup.

### Quorum policy (turns hazard b into a declared choice)

```python
class FusionPolicy(BaseModel):                # pydantic, extra="forbid"
    per_worker_timeout_s: float = 30.0
    quorum: int = 1                           # MIN real responders to proceed
    on_quorum_fail: Literal["raise"] = "raise"   # fusion fails loud, loop sees a model error
    judge_failure: Literal["fallback_code", "raise"] = "fallback_code"
    code_fallback_rule: Literal["concat", "majority"] = "concat"

    def enforce_quorum(self, outcomes) -> list[ModelResponse]:
        ok = [o.response for o in outcomes if o.response is not None]
        failed = [o for o in outcomes if o.error]
        for o in failed:
            logger.warning(f"fusion worker failed: {o.worker_id} ({o.error})")  # visible
        if len(ok) < self.quorum:
            raise FusionQuorumError(                 # FAIL LOUD -> loop's model-error path
                f"fusion quorum not met: {len(ok)}/{len(self._workers)} "
                f"responders, need {self.quorum}; failures={[o.error for o in failed]}"
            )
        return ok                                    # vote on the N-k survivors, explicitly
```

Decision: **vote on N-1 (survivors) IF quorum is met; otherwise raise.** `quorum=1` default =
"as long as ≥1 worker answered, aggregate what we have" (degraded-but-honest, every drop
logged). Set `quorum=2` (or `=N`) when you require true consensus and would rather fail than
answer on a partial panel. Either way the choice is *declared in config*, never implicit.

For **e2e #6** specifically (forced cross-vendor proof), use `quorum: 2` so the test genuinely
fails if only one vendor responded — that is what makes "BOTH vendors truly called" a real
assertion rather than a single-vendor pass dressed up as fusion.

### Judge-failure fallback

The LLM-judge is itself a model call — it can time out or 500. Policy: **`judge_failure:
fallback_code`** (default) — on judge failure, log WARNING and fall back to the configured
`code_fallback_rule` (default `concat`, which never drops information). `judge_failure: raise`
for callers who consider a judge-less answer unacceptable. The fallback path is explicit
(logged + tested), not a bare `try/except`. The judge call is also wrapped in
`wait_for(per_worker_timeout_s)`.

### Negative-path e2e (L9 asks for this)

Add a row: *fusion with one worker pointed at a dead endpoint + `quorum=1`* → answer still
produced from the survivor, **WARNING logged naming the failed worker**; and
*`quorum=N` with one dead worker* → fusion raises, loop surfaces a clean model error (no hang,
bounded by `per_worker_timeout_s`).

---

## Tool-call aggregation

**The hardest part.** Voting on text is easy; voting on *actions* is not. If worker A says
"call `read_file(path=x)`" and worker B says "call `web_search(q=y)`", you cannot "average" two
tool calls — and you must not execute *both* if they are alternatives, nor fabricate a third.
A wrong aggregation here causes the agent to take a real action no single model actually
requested. Industry guidance is thin on this exact problem (most ensemble work votes on final
text); the design below is derived from first principles + self-consistency-on-structured-output
research (ranked/semantic voting over JSON, ACL 2025; SLOT structured-output, EMNLP 2025).

### Core rule: aggregate the DECISION, never synthesize a new action

The unit of agreement is the **whole tool-call decision** of a worker: the set
`{(name, normalized_args)}` plus the `finish_reason`. fusion picks **one worker's decision
verbatim** to return — it never merges args field-by-field and never invents a tool call. The
loop then executes exactly what one real model asked for. This keeps the action provenance
clean (every executed tool call traces to a specific worker) and avoids the catastrophic
"Frankenstein call."

### Algorithm (code aggregator, ensemble)

1. **Partition by response type.** Each worker response is either *text* (`finish_reason
   ∈ {stop,length}`, no tool_calls) or *action* (`finish_reason == "tool_calls"`). These are
   not comparable — a text answer and a tool request are different *kinds* of move.
2. **Vote on the move type first.** Majority of workers want to act vs answer? Two sub-cases:
   - **Tie or text-majority** → aggregate text via the normal text rule (majority/judge).
   - **Action-majority** → proceed to step 3 on the action subset.
3. **Canonicalize each action set.** For a worker, build a *signature*:
   `sorted([(name, canonical_json(args)) for tc in tool_calls])`. Canonicalization =
   sort keys, normalize numbers/whitespace, drop tool-call `id` (vendor-specific, not
   semantic). Two workers "agree" iff signatures match.
4. **Vote on signatures.** Pick the action set with the most votes (self-consistency over
   structured output). Ties → judge, or fall to worker-priority order.
5. **Return that worker's response VERBATIM** — original `tool_calls` (with that worker's
   `id`s, which the loop will echo back in the `tool` result message). Do **not** rebuild the
   tool_calls from the canonical form; round-tripping risks dropping a field the model
   intended.

```python
def aggregate_actions(responses) -> ModelResponse:
    sigs = {}                                   # signature -> [responses]
    for r in responses:
        sig = canonical_signature(r.message.tool_calls)
        sigs.setdefault(sig, []).append(r)
    winner_sig = max(sigs, key=lambda s: len(sigs[s]))     # most-voted action set
    return sigs[winner_sig][0]                  # one real worker's response, untouched
```

### Why not field-level merge or "execute the union"

- **Union-execute is unsafe**: alternatives become simultaneous actions (search *and* delete).
- **Field-merge fabricates**: `path` from A + `path` from B = a path neither model asked for.
- **Synthesizing tool_calls with a judge is unsafe**: a judge writing fresh JSON args can
  hallucinate an argument; the executed action would have no model-grounded provenance.
  → LLM-judge in tool-call situations runs in **`pick-best` mode only** (choose which worker's
  *existing* decision to honor); it never authors new tool_calls. This is the single most
  important safety constraint of the whole subsystem.

### LLM-judge for tool decisions (pick-best, structured)

When the code vote ties or workers disagree on *which* action, hand the judge the original
request + each worker's proposed action set and ask it to **return the index** of the best
proposal (constrained output: `{"choice": <int>}` via `tool_choice` or a tight schema), then
return that worker's response verbatim. The judge selects; it does not author.

### finish_reason and the loop contract

The returned `ModelResponse.finish_reason` is the winner's (`"tool_calls"`), so the loop
dispatches tools normally. Because we return one worker's verbatim message, the tool-call
`id`s are internally consistent with what the loop will reference in the following `tool`
result message — no id remapping needed. **Subtlety:** after tools run, the *next* turn's
fusion call must re-fan-out with the updated history; only the winning worker "saw" its own
prior tool_call, but since fusion replays the *full shared message list* (including the
assistant tool_call + tool result) to *all* workers each turn, every worker gets a coherent
history. This is fine — workers are stateless per call; consistency comes from the shared
message list, not from per-worker memory.

### Router shape + tools

Router forwards one worker verbatim, so tool-calls pass through with zero aggregation — the
clean case. The only requirement: the router must forward the **same `tools`** list it was
given so the chosen worker can actually call them.

---

## Cross-vendor (Anthropic + OpenAI/Azure via LiteLLM)

This is e2e #6's whole point, and it is nearly free because of Decision #28: every worker is a
`LiteLLMProvider` on a different model. LiteLLM normalizes Anthropic Messages ↔ OpenAI formats
both ways (docs.litellm.ai), so fusion only ever sees Alfred's owned `ModelResponse` — it does
*not* know or care that worker[0] is Claude and worker[1] is GPT.

Specifics that matter for fusion:

- **Tool-call normalization is the enabler.** LiteLLM maps both vendors' tool calls to the
  OpenAI-shape `tool_calls[].function.{name,arguments}`, and `provider-layer.md`'s
  `_to_response` parses that into Alfred `ToolCall(name, arguments: dict)`. So the
  **canonical signature** in the tool-call section compares apples to apples across vendors —
  cross-vendor action voting works without per-vendor code.
- **`tool_choice` mapping.** LiteLLM maps `auto`/`required`/`none`/specific to each vendor's
  equivalent. fusion forwards the *same* `tool_choice` to all workers so the move-type vote is
  fair (don't force one vendor to act and let another answer freely).
- **Message sanitization.** Set `litellm.modify_params = True` at the `LiteLLMProvider`
  boundary (not in fusion) so Anthropic's strict tool-message rules (orphaned tool_calls /
  results, empty content) are auto-fixed — relevant because fusion replays the *same* shared
  history (built from whichever worker won last turn) to *all* workers, and a history shaped
  by OpenAI conventions must still be accepted by Anthropic. This is a real cross-vendor
  hazard; sanitization is the mitigation. (Belongs in provider-layer, flagged here because
  fusion is what makes mixed-convention histories happen.)
- **Cost/latency asymmetry.** Vendors differ in latency; `per_worker_timeout_s` must be set
  for the *slowest acceptable* vendor, and `usage` summing must read each vendor's normalized
  `cached_tokens` (LiteLLM `usage.prompt_tokens_details.cached_tokens`, Decision #29) so
  fusion cost accounting is correct across vendors.
- **Env/secrets.** Workers reference different `env_key`s (`ANTHROPIC_API_KEY`,
  `AZURE_OPENAI_API_KEY`) per Decision #26; Azure via the local key-proxy `127.0.0.1:8888`.
  fusion does nothing special — each worker carries its own provider config.
- **Cache.** Each worker caches independently against its own vendor (Anthropic ephemeral
  breakpoint passthrough vs OpenAI prefix-auto-cache). Because all workers receive the *same*
  frozen prefix (Decision #21/#29), each gets its own cache hit. fusion does not interfere
  with per-worker cache.

**e2e #6 pass criterion (restate):** trace/log shows BOTH vendors truly called (two distinct
`model` ids in worker outcomes) + aggregator voted across both outputs. Use `quorum: 2` so a
single-vendor response cannot pass.

---

## Industry refs (URLs)

- Mixture-of-Agents (proposers + aggregator; peers improve when shown each other's answers) —
  https://arxiv.org/html/2406.04692v1
- MoA explainer — https://a-nikishaev.medium.com/mixture-of-agents-moa-improving-llm-quality-through-multi-agent-collaboration-eb0bcbbdbe9f
- MOSAIC (mixture-of-agent scheduling, adaptive aggregation + inference concurrency, 2026) —
  https://arxiv.org/html/2606.03014v1
- LLM-based multi-agent orchestration survey (2026) — https://www.preprints.org/manuscript/202604.2147
- LLM orchestration frameworks/gateways 2026 — https://aimultiple.com/llm-orchestration
- LLM-juries / panel-of-judges ("diverse imperfect judges > correlated perfect"; dynamic
  confidence weighting) — https://orq.ai/blog/llm-juries-in-practice
- LLM-as-a-judge best practices (order randomization, rubric, familial bias) —
  https://agenta.ai/blog/llm-as-a-judge-guide-to-llm-evaluation-best-practices
- Self-consistency = majority vote / empirical mode —
  https://www.emergentmind.com/topics/self-consistency-in-language-models
- Semantic Voting (self-evaluation-free, cluster rationales by similarity, 2509.23067) —
  https://arxiv.org/html/2509.23067v2
- Ranked-voting self-consistency (Borda / IRV / RRV, ACL 2025 findings 744) —
  https://aclanthology.org/2025.findings-acl.744.pdf
- LLM fan-out: self-consistency, consensus, voting patterns —
  https://www.kinde.com/learn/ai-for-software-engineering/workflows/llm-fan-out-101-self-consistency-consensus-and-voting-patterns/
- LLM aggregator (consensus / weighted / synthesis; handling disagreement) —
  https://councilmind.online/blog/llm-aggregator
- Ensemble code generation (similarity-based selection, consensus among candidates) —
  https://arxiv.org/html/2503.15838v1
- asyncio Coroutines & Tasks (wait_for cancels on timeout; gather return_exceptions) —
  https://docs.python.org/3/library/asyncio-task.html
- asyncio.gather() timeout patterns — https://superfastpython.com/asyncio-gather-timeout/
- gather vs as_completed vs TaskGroup (TaskGroup cancels siblings on first error) —
  https://tildalice.io/asyncio-gather-as-completed-taskgroup-patterns/
- LiteLLM Anthropic provider (tool calling, format normalization) —
  https://docs.litellm.ai/docs/providers/anthropic
- LiteLLM tool calling & function integration (tool_choice mapping, response normalization) —
  https://deepwiki.com/BerriAI/litellm/8.1-tool-calling-and-function-integration
- LiteLLM message sanitization for Anthropic tool calling (modify_params) —
  https://docs.litellm.ai/docs/completion/message_sanitization

---

## Open questions

1. **Multi-layer MoA?** MVP ships single-layer (proposers→aggregator). Multi-layer (aggregated
   output fed back as context to a 2nd proposer round) gave the biggest MoA gains but multiplies
   cost/latency and complicates the "one `complete()` call" boundary. Defer; flag as the obvious
   v2 lever. Does the experiment bench want to measure layer-count lift?
2. **Streaming through fusion.** MVP = aggregate-then-emit-one-delta (cannot token-stream a vote).
   Router *could* stream its single forward. Worth a separate streaming code path for router, or
   accept the uniform buffered behavior? (Leaning: uniform, KISS — revisit if TUI latency hurts.)
3. **Panel-of-judges vs single judge.** Research favors a panel for reliability, but that is
   N judge calls. MVP = single judge + cross-family guidance. Make panel a config option now or
   defer? (Leaning: defer; the aggregator ABC already allows a `PanelJudgeAggregator` later.)
4. **Tool-call vote when ALL workers disagree (no signature has a majority, no tie to break).**
   Current fallback = worker-priority order (or judge pick-best). Is "trust the highest-priority
   worker" the right default, or should total disagreement on an *action* force a text-only
   answer ("models disagree on what to do") to avoid a low-confidence real action? This is a
   safety/utility tradeoff worth an explicit decision.
5. **Per-worker tool subset.** Spec assumes all workers get the same `tools`. Could a router
   route by *tool capability* (worker X is better at code tools)? That blurs into handoff
   territory (Decision #23). Keep fusion uniform-tools; route-by-capability stays in handoff.
6. **Quorum default for non-e2e use.** Default `quorum=1` (answer if any survive) vs `quorum=2`
   (require consensus). MVP default is 1 (availability); e2e #6 overrides to 2 (proof). Confirm
   1 is the right *product* default, not just the test default.
