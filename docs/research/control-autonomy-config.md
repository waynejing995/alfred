# Control Plane — autonomy switch + unified config

Date: 2026-06-15
Module: `agentkit/control/` (Ring-1 discipline: the gate is kernel-owned; config is the SSoT all rings read)
Spec refs: §3.3 (provider config), §6 (Control & Configuration), §6.1 (autonomy), §6.2 (config), §6.3 (alfred-agent);
Decisions #13, #20, #20c, #24, #26, #28, #30a, #31; Eng findings H3, L8; e2e #6, #13, #14, #15.
Primary sources: Pydantic Settings / Unions docs, Claude Code permission model, LangGraph durable-interrupt HITL, NIST agent-oversight (2026). See ## Industry refs.

---

## Module scope

Two tightly-coupled control surfaces that together form Alfred's **single source of truth for
"what the agent is" and "how far it may act on its own"**:

1. **AutonomyGate** — the kernel-owned e-stop. A single `off/assist/auto` switch (default
   `assist`) that EVERY auto-loop (goal continuation, distill, dream, evolve) must consult
   before acting, plus the runtime toggle event and the held-proposal primitive that the two
   per-loop confirm gates (evolve merge, distill new-skill) reuse.
2. **AgentConfig** — one pydantic model = config SSoT. YAML/JSON primary load + direct
   code construction (SDK) share ONE schema; `extra="forbid"`; layered deep-merge
   (bundled → user → project → env/code); the recursive `{type, params}` assembly pattern for
   every registry-backed component (model/fusion/skill_sources/mcp); `env_key` secret indirection.

In scope (this module OWNS these — other modules reference, don't redefine):
- The `AutonomyGate` object + `gate.check(loop)` API the four subsystems call.
- The `autonomy.changed` runtime event + the rule that `autonomy` is reload-able live (unlike the
  rest of the config, which is frozen-at-start).
- The held-proposal primitive (`Proposal` + `ProposalStore`) the confirm gates share.
- The full `AgentConfig` schema + layered loader + the registry-dispatch resolver for `{type,params}`.
- `env_key` resolution at provider-construction time; the no-plaintext-secret invariant.
- The `alfred-agent` self-edit guardrail: the validator that rejects agent-originated `autonomy` writes.

Out of scope (owned elsewhere, this doc only defines the contract they call):
- The actual auto-loop logic (goal/distill/dream/evolve research docs) — they CALL `gate.check`.
- The skill loader, provider construction internals, mcp client — they are `{type,params}` consumers.
- The event-bus mechanism (kernel:event-bus) — `autonomy.changed` is just another self-describing event.

---

## Autonomy gate

### The control object (kernel-owned, Ring-1)

`autonomy` is NOT just a config field — it is a live kernel control register. The config seeds its
initial value; thereafter it can be toggled at runtime (e2e #14 toggles it mid-run) and is the ONE
config value exempt from frozen-at-start semantics (everything else reloads only on restart, §6.3).

```python
# agentkit/control/autonomy.py
from enum import Enum

class Autonomy(str, Enum):
    OFF    = "off"      # all auto-loops halt — manual only
    ASSIST = "assist"   # auto-loops run but every WRITE/ACT is confirm-required (DEFAULT)
    AUTO   = "auto"     # full auto — no confirmation

class AutonomyGate:
    """Single e-stop register. Owned by the loop/daemon, injected into every Ring-3 auto-loop."""
    def __init__(self, initial: Autonomy, bus: EventBus):
        self._level = initial
        self._bus = bus

    @property
    def level(self) -> Autonomy:
        return self._level

    def set(self, level: Autonomy, *, source: str) -> None:
        if level == self._level:
            return
        old, self._level = self._level, level
        # Push, don't poll: state owner emits; subscribers never re-read a flag in a loop.
        self._bus.emit(AutonomyChanged(old=old, new=level, source=source))

    def allows_auto(self) -> bool:
        """off → halt entirely. assist/auto → loop may RUN (gating happens at the WRITE step)."""
        return self._level is not Autonomy.OFF

    def requires_confirm(self) -> bool:
        """assist → hold for human. auto → write straight through."""
        return self._level is Autonomy.ASSIST
```

Design choice — **two-question API, not one boolean.** `off` gates *running the loop at all*;
`assist` gates *the irreversible action inside the loop*. Collapsing these into one check forces
each subsystem to re-derive the distinction. So the gate exposes exactly the two questions the
spec's three levels imply: "may I run?" (`allows_auto`) and "must I ask first?" (`requires_confirm`).

### How each of the 4 auto-loops gates (uniform 3-step contract)

Every auto-loop follows the SAME shape. This is the contract the goal/distill/dream/evolve docs
already reference (distill calls it "gate FIRST, L8"):

```
trigger fires (idle / tick / turn_end / skill_used)
  1. gate.allows_auto()?         → False (off): DEBUG "<loop> halted by autonomy=off", STOP   (e2e #14)
  2. loop's own trigger filters   → not met: DEBUG skip, STOP   (e.g. distill batch_min, evolve trace filter)
  3. produce result, then at the WRITE/ACT step branch on gate.requires_confirm():
       auto   → act immediately
       assist → hold a Proposal, surface it, wait for human accept/reject
```

Per-loop mapping (the "ACT" that step-3 gates differs by loop):

| Loop | Trigger | `off` halts | `assist` confirm-gates the... | Notes |
|---|---|---|---|---|
| **goal** continuation | `turn_end` (unmet & not paused) | yes — no self-continue | the *self-continue* (inject "continue" next turn) | budget cap is an independent safety net (#19); off ⇒ no auto-drive |
| **distill** new-skill | `idle`/`tick` | yes | the *SKILL.md write* (per-loop gate #3, §6.1) | confirm-required by default INDEPENDENT of assist (see ## Gate composition) |
| **dream** housekeeping | `idle`/`session_end` | yes | the *memory mutation* (dedup/merge/decay) | governed by global autonomy ONLY — no per-loop gate (§5.3) |
| **evolve** merge | `skill_used` accumulation | yes | the *variant merge* (per-loop gate #2, §6.1) | confirm-required by default INDEPENDENT of assist |

The subsystem-side check is one line; the gate is injected, not polled:

```python
# inside any auto-loop subsystem
if not self.gate.allows_auto():
    logger.debug("{} halted: autonomy=off", self.name)   # fail-quiet: a filter, not an error
    return
... # do the work
self.proposals.decide(proposal, auto_accept=not self.gate.requires_confirm())
```

### Runtime toggle (event, not poll)

`autonomy.changed` is a normal self-describing kernel event (kernel:event-bus, Decision #9), emitted
by `AutonomyGate.set()`. Sources: CLI/TUI command (`/autonomy off|assist|auto`), SSE control endpoint,
or programmatic SDK call. The daemon's long-lived auto-loops do NOT poll a flag — they hold the
injected `AutonomyGate` reference and read `.level` only at their own trigger time (so a toggle takes
effect at the next trigger boundary, which is the correct, race-free granularity for an e-stop). e2e
#14: set `off` mid-session → goal stops self-continuing, distill/evolve don't fire, log shows gate block.

**One subtlety (fail-loud):** because `autonomy` is the ONE live-reloadable field, it must NOT be
re-frozen by a config reload. When H3's "explicit reload trigger" re-reads `AgentConfig`, the reload
path MUST preserve the *current runtime* autonomy level unless the human explicitly changed it via
`/autonomy` — a stale file value silently re-raising autonomy would defeat the e-stop. Documented as
an invariant + test.

---

## Gap answers (L8)

> **L8 — autonomy gate must land WITH/BEFORE the first auto-loop (with trace store, before distill).**

**Answer: yes, and it's cheap because the gate is tiny and has no dependencies.** Concretely:

1. **Build order.** The §8 dependency chain is `… → trace store → distill/dream/evolve/goal → …`.
   `AutonomyGate` (≈40 lines: enum + register + 2 query methods + 1 event) and the `Proposal`/
   `ProposalStore` primitive (≈60 lines) are a SINGLE implementation unit that lands **immediately
   after trace store and before the FIRST auto-loop**. Since goal (a `turn_end` self-driver) is the
   earliest auto-loop in the chain, the gate must in fact precede *goal*, not just distill — the unit
   lands at the `trace store → [GATE] → goal` seam. One commit (Decision #27), self-contained: gate +
   proposal store + `autonomy.changed` event + the `AgentConfig.autonomy` field + e2e #14 skeleton.

2. **Why before, not after.** The gate is the e-stop (cybernetics #8, Decision #20). Shipping any
   self-driving loop WITHOUT the halt already in place is exactly the "no global e-stop → runaway"
   finding that motivated #20c. An auto-loop that exists for even one commit without a gate is an
   unguarded feedback loop. So the ordering is a safety invariant, not a nicety.

3. **No back-fit risk.** Because every auto-loop consults the gate via the same injected
   `gate.check`/`allows_auto` contract (above), adding the gate first means each later loop is born
   gated — there is never a "retrofit the gate into N existing loops" step. The gate's existence is a
   precondition in each loop's constructor signature (`def __init__(self, ..., gate: AutonomyGate)`),
   so a loop literally cannot be constructed without one. That's the structural enforcement of L8.

4. **How a subsystem checks the gate before acting** — see the 3-step contract + one-line check above.
   The check is: `allows_auto()` at trigger (step 1), `requires_confirm()` at the write/act step (step 3).
   Reversible reads/analysis happen freely between; only the irreversible write is confirm-gated.

---

## AgentConfig schema

### Top-level shape (the SSoT)

```python
# agentkit/control/config.py
from pydantic import BaseModel, ConfigDict, Field

class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)  # typo → crash at startup (§7 Fail-Loud)

    # --- control plane ---
    autonomy: Autonomy = Autonomy.ASSIST                    # seeds the live gate; default assist
    gates: GateConfig = Field(default_factory=GateConfig)   # per-loop confirm gates (below)

    # --- assembly-type components: {type, params} (resolved against registries) ---
    model: ComponentSpec                                    # single | fusion | router (models registry)
    skill_sources: list[SkillSourceSpec] = Field(           # ordered = precedence (#12)
        default_factory=lambda: [SkillSourceSpec(type="dir", params={"path": "./skills"}),
                                 SkillSourceSpec(type="dir", params={"path": "~/.myagent/skills"}),
                                 SkillSourceSpec(type="bundled", params={})])
    mcp: list[ComponentSpec] = Field(default_factory=list)  # each {type: mcp, params:{transport,...}}
    middleware: list[ComponentSpec] = Field(default_factory=list)  # 5th registry (#30a)

    # --- plain scalar config (not assembly-type) ---
    skill_filter: SkillFilter = Field(default_factory=SkillFilter)  # include/exclude_tags + disable[]
    budget: BudgetConfig = Field(default_factory=BudgetConfig)      # iteration cap, goal safety cap
    session: StoreSpec = ...        # store impls are also {type,params} (sqlite default)
    memory:  StoreSpec = ...
    trace:   StoreSpec = ...

class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evolve_merge:    Literal["confirm-required", "auto"] = "confirm-required"   # #20c gate 2
    distill_new_skill: Literal["confirm-required", "auto"] = "confirm-required" # #20c gate 3
```

### The recursive `{type, params}` assembly pattern

The single most important schema decision. EVERY registry-backed component is declared the same way:
a `type` string (the registry key) + a `params` dict. **It is recursive**: a value inside `params`
can itself be a `ComponentSpec`, which is precisely how fusion "nests" providers (a fusion's workers
and judge are themselves provider specs). The §6.2 example:

```yaml
model:
  type: fusion
  params:
    strategy: ensemble
    workers:
      - {type: litellm, params: {model: claude-opus-4-8, env_key: ANTHROPIC_API_KEY}}
      - {type: litellm, params: {model: gpt-5.5,        env_key: AZURE_OPENAI_API_KEY}}
    aggregator:
      type: llm_judge
      params: {judge: {type: litellm, params: {model: claude-haiku-4-5, env_key: ANTHROPIC_API_KEY}}}
```

**Schema for the spec itself — open catalog, so NOT a closed discriminated union.** Pydantic's
discriminated-union pattern (the obvious 2026 idiom) routes a `type` tag to one of a *fixed* set of
models. But Alfred's registries are an OPEN extension surface (Decision #8: "mechanism open, catalog
converged" — plugins register new types). A closed `Union[LiteLLMSpec, FusionSpec, ...]` would
re-close what the registry deliberately leaves open and force a schema edit per plugin. So
`ComponentSpec` is a thin, generic two-field model; the `type→class` routing happens at **construction
time against the live registry**, not at parse time against a frozen union:

```python
class ComponentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str                       # registry key (validated against the registry, fail-loud)
    params: dict[str, Any] = Field(default_factory=dict)

    # params is validated by the TARGET component's own pydantic params-model when it is built.
```

Two-phase validation (this is the key trade-off, stated explicitly):
- **Parse phase** (`from_yaml` / construction): validate STRUCTURE — every node is `{type:str, params:dict}`,
  `extra=forbid` catches typo'd top-level keys, the recursion is well-formed. Cheap, no registry needed.
- **Build/resolve phase** (when the agent is assembled): the resolver looks up `type` in the registry,
  fetches that component's declared `Params` pydantic model, and validates `params` against it —
  recursing into nested `ComponentSpec`s first (workers/judge built before fusion). Unknown `type` →
  fail-loud `UnknownComponentType`. Bad `params` → fail-loud with the component's own field errors.

```python
def resolve(spec: ComponentSpec, registry: Registry) -> Any:
    entry = registry.get(spec.type)                      # KeyError → UnknownComponentType (fail-loud)
    params = {k: (resolve(v, registry) if isinstance(v, ComponentSpec) else v)   # recurse nested specs
              for k, v in spec.params.items()}
    validated = entry.params_model.model_validate(params)  # per-component params schema (fail-loud)
    return entry.factory(validated)                        # construct the live object
```

Each registry entry thus carries `{factory, params_model}` — the `params_model` is where the *real*
typed validation lives (e.g. `LiteLLMParams(model: str, env_key: str | None, base_url: str | None,
http_headers: dict, query_params: dict)` per Decision #26). This keeps `AgentConfig` itself stable
while every component owns and validates its own param schema (SSoT per component). Registering a new
plugin type adds a registry entry; it does NOT touch `AgentConfig`. A plugin author MAY optionally
register their `params_model` for early (parse-phase) validation, but it is not required.

### Layered loading (deep-merge order)

Following 2026 pydantic-settings practice (custom `settings_customise_sources` ordering + a deep-merge
across layers), Alfred composes four layers, **later overrides earlier** (§6.2):

```
bundled defaults (shipped) → ~/.myagent/config.yaml → ./agent.yaml → env / code overrides
   (lowest precedence)                                                  (highest precedence)
```

Merge is a **recursive deep-merge for dicts/models, replace for lists** (standard layered-config
semantics: `pydantic-config` / the pydantic-settings deep-merge recipe both do dict-merge). The
list-replace rule matters for `skill_sources` and `workers`: a project that redefines `model.params.
workers` REPLACES the whole worker list (you don't want a half-merged fusion). Scalar/dict fields
deep-merge so a project can override `model.params.strategy` without restating the workers... EXCEPT
when `type` changes — **if a deeper layer changes a node's `type`, that node's `params` is replaced
wholesale, not merged** (merging `litellm` params into a `fusion` node is nonsense). This is the one
non-obvious merge rule and it is enforced in the merge function + tested.

```python
def deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if k == "type" and base.get("type") not in (None, v):
            return dict(over)            # type changed → replace this whole node
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v                   # lists & scalars: replace
    return out
```

Env layer: scoped prefix `ALFRED_` with nested delimiter (`ALFRED_MODEL__PARAMS__MODEL=...`) per
pydantic-settings `env_nested_delimiter`, applied as the top override layer. Secrets are NEVER read
here as config values — env holds the *key material* that `env_key` indirection points AT (below).

### from_yaml + direct construction share ONE schema

Both entry points produce the same validated `AgentConfig` — the dual-load requirement of Decision #13:

```python
@classmethod
def from_yaml(cls, *paths) -> "AgentConfig":
    merged = {}
    for layer in default_layers(*paths):          # bundled → user → project (each a parsed dict)
        merged = deep_merge(merged, load(layer))
    merged = apply_env_overrides(merged)          # env layer last
    return cls.model_validate(merged)             # SAME validation as direct construction

# direct SDK construction — same schema, no YAML:
cfg = AgentConfig(model=ComponentSpec(type="fusion", params={...}), autonomy=Autonomy.AUTO)
```

`from_yaml` is *only* a dict-assembler that ends in `model_validate`; there is no separate YAML
schema. Direct construction is `__init__`. Both converge on one pydantic model → no schema drift
(the SSoT guarantee). e2e #6/#13 exercise the YAML path; e2e #2 (SDK `import`) exercises direct.

---

## Gate composition

Three controls, two scopes — they compose as **a global e-stop (coarse) over two fine-grained
per-loop confirm gates**. This is the spec's "three-layer" autonomy (§6.1) made precise.

| Control | Scope | Question it answers | Default |
|---|---|---|---|
| `autonomy` (off/assist/auto) | GLOBAL — all 4 loops | "may auto-loops run / must they confirm AT ALL?" | `assist` |
| `gates.evolve_merge` | evolve only | "must an evolve *merge* be confirmed even when global says auto?" | `confirm-required` |
| `gates.distill_new_skill` | distill only | "must a distill *new-skill write* be confirmed even when global says auto?" | `confirm-required` |

### Composition rule (the effective decision is the STRICTER of the two)

For the two loops that have a per-loop gate (evolve, distill), the action proceeds without
confirmation only if BOTH the global switch AND the per-loop gate permit it. Truth table for "does
this write require a human confirm?":

| `autonomy` | per-loop gate | effective for distill/evolve write |
|---|---|---|
| `off` | (any) | **halted** — loop doesn't even run |
| `assist` | confirm-required | **confirm** |
| `assist` | auto | **confirm** (global assist still forces it) |
| `auto` | confirm-required | **confirm** (per-loop gate still forces it — the important cell) |
| `auto` | auto | **proceed** — no confirm |

```python
def must_confirm(gate: AutonomyGate, per_loop: str) -> bool:   # per_loop ∈ {"confirm-required","auto"}
    # AND-of-permits == OR-of-requires == take the stricter.
    return gate.requires_confirm() or per_loop == "confirm-required"
```

The load-bearing cell is **`auto` + `confirm-required` → still confirm.** It means a user who flips
the global switch to full-auto STILL gets a confirmation prompt before a new skill is written or a
skill variant is merged — the two highest-consequence, hardest-to-reverse actions. That is the whole
point of having per-loop gates *in addition to* the global switch: the global switch is the e-stop for
*everything*; the per-loop gates are durable, default-on guards on the two irreversible writes that
survive even an over-eager global `auto`. goal and dream have NO per-loop gate, so for them the global
switch is the only control (goal also has its independent budget cap; dream is fully reversible-ish
via memory history).

### The shared confirm primitive (held proposal, durable)

Both per-loop gates AND global `assist` use ONE mechanism — a held `Proposal`, not bespoke per-loop
plumbing. This matches the 2026 durable-interrupt HITL pattern (LangGraph `interrupt()`/`Command(resume=)`:
pause, PERSIST state, wait, resume) rather than a blocking in-process prompt — essential because in a
headless daemon (Decision #6) there may be no human attached at proposal time.

```python
class Proposal(BaseModel):
    id: str
    loop: str                 # "distill" | "evolve" | "goal" | "dream"
    kind: str                 # "new_skill" | "merge" | "continue" | ...
    payload: dict             # diff / SKILL.md / variant ref — enough to render + to apply on accept
    status: Literal["pending", "accepted", "rejected"] = "pending"

class ProposalStore:          # persisted (survives daemon restart — durable, not in-memory)
    def hold(self, p: Proposal) -> str: ...     # store pending, emit "<loop>.proposed"
    def decide(self, id, accept: bool): ...      # apply-or-discard, emit "<loop>.accepted|rejected"
```

Flow (the distill doc already walks this for new-skill; it generalizes):
`produce result → must_confirm? → no: apply now → yes: ProposalStore.hold() → emit <loop>.proposed →
event-bus → SSE /events → CLI/TUI renders → human '/distill accept <id>' → decide(accept) → apply`.
In `assist` with no attached human, proposals simply sit `pending` (visible via `/… list`) — never
lost, never auto-applied. This is the "gate is just a held proposal keyed by id" primitive the distill
research references; it lives HERE (control plane) so distill/evolve/goal don't each reinvent it.

---

## Self-edit safety

The `alfred-agent` skill (Decision #24, §6.3) lets the agent edit its own `AgentConfig` file. Two
guardrails make this safe; both are enforced in THIS module.

### 1. Restart-to-apply (frozen-config semantics)

`AgentConfig` is `frozen=True` and loaded once at process start (§6.3, mirrors skill/memory freeze
for cache stability). The `alfred-agent` skill edits the YAML FILE on disk; the change does NOT
mutate the running config. It takes effect only on **daemon/CLI restart** (or H3's explicit reload
trigger for a long-lived interactive daemon). Rationale: explicit restart > silent next-turn mutation
(more controllable, Fail-Loud), and it preserves the frozen-prefix cache invariant (a mid-session
config change would invalidate the cached system prompt). e2e #15: agent edits model via skill →
restart → new model in effect.

**The one exception is `autonomy`** — it is the live register (above), togglable without restart. But
the agent is forbidden from writing it at all (next guardrail), so the agent never exercises the live
path; only a human does.

### 2. The `autonomy`-field self-edit ban (e-stop integrity)

**Threat model:** if the agent could write `autonomy` in its own config file, an agent in `assist` (or
even a human-set `off`) could escalate itself to `auto`, defeating the e-stop — the exact
self-escalation cybernetics #8 / Decision #20c exist to prevent. So `autonomy` (and `gates.*`, by the
same logic — escalating a per-loop gate is the same attack) is **forbidden for agent-originated writes.**

**How enforced — provenance-aware validation, not just "the skill is polite."** A prose instruction in
the SKILL.md is not a control; it must be a hard validator. Concretely:

- The `alfred-agent` skill writes config edits through a **single dedicated tool** (`edit_own_config`),
  NOT a generic file-write — so the write path is identifiable as agent-originated.
- That tool runs the proposed new file through a `validate_self_edit(old, new, origin="agent")` check
  BEFORE persisting. The check diffs old vs new and **rejects the write if `autonomy` or any
  `gates.*` field changed** when `origin == "agent"`:

```python
SELF_EDIT_FORBIDDEN = {"autonomy", "gates"}     # e-stop integrity boundary

def validate_self_edit(old: dict, new: dict, *, origin: str) -> None:
    if origin != "agent":
        return                                   # human edits: unrestricted
    for field in SELF_EDIT_FORBIDDEN:
        if old.get(field) != new.get(field):
            raise SelfEditForbidden(             # fail-loud, surfaced as tool-result to the model
                f"agent may not modify '{field}' (e-stop integrity, Decision #24)")
```

- Defense in depth: even if a bug let an agent-written file through, the value is only *seeded* at
  start; the LIVE autonomy register is changed exclusively by `AutonomyGate.set()` whose only callers
  are human-facing (`/autonomy` command, SSE control, SDK) — never the agent's tool surface. So the
  agent has no path to the live register AND no path to the seed. Two independent barriers.
- e2e #15 asserts the rejection: "agent's attempt to edit `autonomy` field is rejected."

The ban is enforced at the WRITE boundary (provenance known there), not by trying to parse intent —
the cleanest place, and it composes with `extra=forbid` (a sneaky renamed field still fails parse).

---

## Secret handling

`env_key` indirection (Decision #26, learned from Codex `[model_providers.x]`): **config stores the
NAME of an env var, never the plaintext key.** This honors the KB `llm-prompt-and-boundary-contracts`
rule that secrets don't enter owned config (which would otherwise land in git, in trace dumps, in
`/config` views, in the agent's own readable config file — a leak surface multiplied by Alfred's
SSE/trace/self-edit features).

Mechanism:
- Provider params declare `env_key: str` (the env var name), plus optional `base_url`/`http_headers`/
  `query_params` for the proxy gateway (§3.3, Decision #26).
- The plaintext key is resolved **only at provider-construction time**, inside the provider factory,
  by reading `os.environ[spec.env_key]` — never stored back onto the config object, never serialized.
- Missing env var → **fail-loud at construction** (`MissingSecret: env var ANTHROPIC_API_KEY not set`),
  not a silent empty-string key that fails opaquely on the first API call (CLAUDE.md: missing config
  → crash at startup, not on the Nth action).

```python
class LiteLLMParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    env_key: str | None = None                 # NAME of env var; never the key itself
    base_url: str | None = None
    http_headers: dict[str, str] = Field(default_factory=dict)
    query_params: dict[str, str] = Field(default_factory=dict)

def build_litellm(p: LiteLLMParams) -> LiteLLMProvider:
    api_key = None
    if p.env_key:
        try:
            api_key = os.environ[p.env_key]
        except KeyError:
            raise MissingSecret(f"env var {p.env_key} not set")   # fail-loud at startup
    return LiteLLMProvider(model=p.model, api_key=api_key, base_url=p.base_url, ...)
```

Consequences that keep secrets out of every derived view:
- **Trace store / events / SSE** carry `env_key` (a name), never the resolved key — safe to log/stream.
- **`alfred-agent` self-edit** reads/writes a file that contains only `env_key` names — the agent can
  reconfigure which env var to use but can never read or write the actual secret (it's not in the file).
- **`extra=forbid`** also helps: a user who fat-fingers `api_key: sk-...` into the YAML gets a startup
  crash ("unexpected field api_key"), which both stops the leak and teaches the `env_key` pattern.

---

## Industry refs

- Pydantic Settings (custom sources, `env_nested_delimiter`, nested-env precedence) — https://docs.pydantic.dev/latest/concepts/pydantic_settings/
- Pydantic Unions / discriminated (tagged) unions — why a CLOSED union is the wrong fit for an OPEN registry catalog — https://docs.pydantic.dev/latest/concepts/unions/
- pydantic-config (layered file merge, deep-merge semantics) — https://pypi.org/project/pydantic-config/
- YAML layered-config + deep-merge recipe discussion — https://github.com/pydantic/pydantic-settings/issues/185
- Claude Code permissions — deny>ask>allow ordering, `acceptEdits`/`plan`/bypass modes (maps to off/assist/auto + per-tool gates) — https://code.claude.com/docs/en/permissions
- Claude Code permission modes (2026) — https://www.explainx.ai/blog/claude-code-permission-modes-explained-2026
- LangGraph durable execution + `interrupt()`/`Command(resume=)` HITL (the held-proposal/durable-pause pattern, daemon-safe) — https://docs.langchain.com/oss/python/langgraph/durable-execution
- LangGraph persistence + human-in-the-loop — https://medium.com/@iambeingferoz/persistence-in-langgraph-building-ai-agents-with-memory-fault-tolerance-and-human-in-the-loop-d07977980931
- Human-in-the-Loop patterns / calibrated autonomy (HITL vs HOTL, tiered autonomy, route-irreversible-for-approval) — https://myengineeringpath.dev/genai-engineer/human-in-the-loop/
- "The Autonomy Gate: multi-level agent evaluation → machine approval" (graduated autonomy framing) — https://vadim.blog/autonomous-agent-evaluation-human-approval/
- HITL approvals/escalation in production (gate at irreversible actions, not reads) — https://www.agno.com/blog/how-to-add-human-in-the-loop-controls-to-ai-agents-that-actually-run-in-production
- NIST agent-oversight initiative (Feb 2026) — opacity of decision chains, e-stop on long-running autonomy — https://www.strata.io/blog/agentic-identity/practicing-the-human-in-the-loop/

---

## Open questions

1. **Per-loop gate granularity for goal/dream.** Spec gives evolve & distill explicit per-loop gates
   but goal (budget cap only) and dream (global-only) none. Should a `gates.goal_continue` /
   `gates.dream_write` be reserved now (cheap field) or added when needed (YAGNI)? Leaning YAGNI — the
   composition rule already supports adding them with zero structural change.
2. **Proposal store durability backend.** `ProposalStore` must survive daemon restart (durable HITL).
   Reuse the session SQLite (one less store) or a dedicated table/file? Interacts with store:session.
3. **H3 reload trigger × live autonomy.** The "preserve runtime autonomy across reload" invariant
   needs a precise rule for the case where the human ALSO edited the file's `autonomy` value between
   reloads — file vs live conflict resolution. Proposed: live wins unless reload is human-initiated
   with an explicit `--adopt-file-autonomy` flag. Needs confirmation with the daemon/H3 owner.
4. **Plugin params_model registration timing.** Optional early (parse-phase) validation requires the
   plugin's `params_model` to be registered before `from_yaml` runs — but plugins register at import.
   Define the load ordering: import plugins → THEN parse config, or two-pass? Affects build order.
5. **`env_key` for non-provider secrets.** mcp HTTP transports may need auth headers with secrets.
   Should `http_headers` values also support `env_key`-style indirection (e.g. `{Authorization:
   {env_key: MCP_TOKEN}}`) rather than plaintext? Likely yes for consistency — flag for mcp module.
