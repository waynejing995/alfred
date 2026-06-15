# Ring-3 Subsystem — Handoff (Multi-Agent Collaboration)

Module research for the Alfred design spec (§5.2, §5.3 subagent row, Decision #23, #23a).
Date: 2026-06-15. Scope: multi-agent collaboration built as an **extension of subagent**,
the payload/event coupling surface, and the isolation invariant the user flagged as
change-prone ("ensure isolation, I may change it later").

---

## Module scope

Handoff = multi-**AGENT** collaboration: each agent has independent context, its own loop
instance, and its own tool set. It is NOT fusion (fusion = N models on the *same* messages
inside one provider call, §5.1). Handoff transfers **control + a structured payload** A→B.

Built as an **extension of subagent (S1)**, NOT a new kernel primitive (Decision #23).
The kernel loop stays multi-agent-unaware; everything multi-agent lives in:

- one `spawn_subagent` **tool** (registry entry) — the existing S1 minimal form,
- one `handoff` **event** (kernel event #7/#23) — the control-transfer signal,
- one **payload schema** — the SOLE coupling surface across agents,
- a **transfer record** written to the trace store (feeds evolve).

Two built-in patterns; swarm/peer is NOT built (plugin seam, Decision #23):

| Pattern | Control | Shape | Industry analogue |
|---|---|---|---|
| orchestrator-worker | **returnable** | parent holds context, spawns isolated worker, result returns (puppeteer lives here) | OpenAI *agents-as-tools*; Anthropic lead→subagent |
| sequential handoff | **one-way** | A completes → payload → B takes over; A does not resume | OpenAI *handoffs* (`transfer_to_X`) |

Out of scope (other modules): loop control flow + the `IterationBudget` API
(kernel:loop-budget, owns the shared-cap/separate-ledger mechanics); trace-store schema
(store-trace, already carries `parent_trace_id`/`agent_role`/`handoff_payload`); event-bus
serialization (kernel:event-bus); git-worktree file isolation (TODO, Decision #30).

This doc owns: how the three pieces compose, the payload schema design, returnable-vs-one-way
control mechanics, and how the four isolation layers are *actually enforced* in code.

---

## Handoff-subagent composition

The key insight from Decision #23: **subagent is the minimal form of orchestrator-worker.**
Handoff is subagent "at higher intensity" — it adds (a) explicit control-transfer semantics
(returnable vs one-way), (b) a typed payload contract, (c) a persisted transfer record. All
three reuse existing kernel surfaces; nothing new lands in Ring 1.

### The single spawn primitive

There is ONE mechanism that creates a child agent: a `Spawner` that builds an isolated
`TurnCtx` (the same struct the loop already takes — see kernel:loop-budget) and runs a fresh
loop on it. Both built-in patterns call it; they differ only in **whether the parent awaits
the result** and **what the parent does with control afterward**.

```python
@dataclass
class AgentSpec:
    """How to construct a child agent. The construction-time half of isolation."""
    role: str                      # 'worker' | 'handoff-target' (→ trace agent_role)
    instructions: str              # child system prompt (frozen at its session_start)
    tool_names: list[str]          # EXPLICIT tool grant — no auto-inherit (#23a layer 2)
    model: ModelProviderRef | None = None   # may differ from parent; default = parent's

class Spawner:
    """The sole child-agent factory. Both patterns route through here (SSoT)."""
    def __init__(self, registries, budget: IterationBudget, trace, bus):
        self._reg, self._budget, self._trace, self._bus = registries, budget, trace, bus

    async def spawn(self, spec: AgentSpec, payload: "HandoffPayload",
                    parent_trace_id: str) -> "HandoffResult":
        child_id = f"{spec.role}:{uuid4().hex[:8]}"
        child_ctx = self._build_isolated_ctx(spec, payload, child_id, parent_trace_id)
        self._bus.emit("handoff", HandoffEvent(                  # the coupling-surface signal
            from_agent=payload.from_agent, to_agent=child_id,
            control=payload.control, payload=payload, parent_trace_id=parent_trace_id))
        result = await run_turn(child_ctx)                       # fresh isolated loop
        self._trace.write_transfer(parent_trace_id, child_id, payload, result)  # feeds evolve
        return HandoffResult.from_turn(child_id, result)
```

### Pattern 1 — orchestrator-worker (returnable): the `spawn_subagent` tool

Exposed to the model as a **tool** (registry entry). The orchestrator keeps its own context
and control; the worker runs to completion; the worker's *result payload* comes back as the
tool result and is appended to the orchestrator's history. This is exactly OpenAI's
**agents-as-tools** pattern and Anthropic's **lead→subagent** pattern.

```python
# tools registry entry — bundled skill `spawn-worker` surfaces this
async def spawn_subagent(role: str, objective: str, output_format: str,
                         tool_names: list[str], ctx: ToolCtx) -> dict:
    grant = ctx.budget.reserve(ctx.agent_id, n=1)          # parent pays 1 to spawn
    if grant is None:
        return {"error": "budget_exhausted"}
    payload = HandoffPayload(
        from_agent=ctx.agent_id, control="returnable",
        objective=objective, output_format=output_format,
        artifacts=[], context_refs=ctx.shareable_refs())     # references, not bodies
    spec = AgentSpec(role=role, instructions=_worker_prompt(objective, output_format),
                     tool_names=tool_names)                   # scoped, NOT inherited
    res = await ctx.spawner.spawn(spec, payload, ctx.trace_id)
    return res.result_payload.model_dump()                   # returns INTO parent history
```

The worker is invisible to the model except through this one tool call + its return value —
the loop dispatches it like any other tool (one dispatch path, SSoT). The orchestrator can
spawn workers **in parallel** by emitting N tool calls in one turn; the loop's
`asyncio.gather` over dispatches (kernel:loop-budget) runs them concurrently against the
shared budget. This matches Anthropic's "lead spawns 3-5 subagents simultaneously, each in
its own context window, never seeing the others."

### Pattern 2 — sequential handoff (one-way): control does not return

A→B where A is **done** and B owns the next response. Modeled after OpenAI's `transfer_to_X`
tool. The difference from Pattern 1 is purely control: the parent loop **terminates its turn**
after the handoff instead of appending a tool result and continuing.

```python
async def handoff_to(target_role: str, payload_fields: dict, ctx: ToolCtx) -> "Transfer":
    payload = HandoffPayload(from_agent=ctx.agent_id, control="one_way", **payload_fields)
    # do NOT reserve-and-return into parent history; signal the loop to hand control over
    return Transfer(spec=AgentSpec(role=target_role, ...), payload=payload)
```

The loop recognizes a `Transfer` return value (a sentinel, like the goal subsystem's
self-continue): instead of `history.append(tool_result)` it ends the parent turn and the
host starts B's turn from the payload. B's trace gets `parent_trace_id = A.trace_id` but A
never resumes. (Returnable would `await` and append; one-way terminates and transfers.)

### Why this is an extension, not a primitive

| Need | Reused surface | New? |
|---|---|---|
| create child agent | `TurnCtx` + `run_turn` (loop) | no |
| budget across agents | `IterationBudget` shared instance (kernel:loop-budget) | no |
| signal a transfer | `handoff` event on the event-bus | event def only (2-line add, #9) |
| typed A→B data | `HandoffPayload` pydantic | **yes — the sole new contract** |
| learning material | trace store `handoff_payload`/`parent_trace_id` columns | columns already exist (store-trace) |

The only genuinely new artifact is **the payload schema**. That is by design: Decision #23a
converges all multi-agent variability onto one surface.

---

## Payload schema

This is **THE sole coupling surface** (#23a layer 4). Every cross-agent byte goes through
it. The user will change collaboration modes, so it must be extensible **without touching
the kernel or the spawn mechanism**. Three design moves make it so.

### 1. Schema-first, references-not-bodies (matches event-payload rule #7)

Like kernel events, the payload carries **references + metadata**, not full message bodies.
Anthropic's artifact pattern is the precedent: "subagents store work in external systems,
then pass lightweight references back to the coordinator… reduces token overhead from
copying large outputs through conversation history." Alfred's external store = session +
trace + memory; the payload carries `*_refs` (ids), and the receiver fetches on demand.

### 2. A stable core + an open `extra` map (the extensibility seam)

The classic SSoT trap (CLAUDE.md) is one concept encoded three ways. So the payload has a
**small frozen core** (control semantics every pattern needs) plus **one typed-but-open
extension dict** for mode-specific fields. New collaboration modes add keys to `extra` (or
register a payload subclass) — they never re-shape the core, never touch the kernel.

```python
from pydantic import BaseModel, Field
from typing import Literal

class HandoffPayload(BaseModel):
    """The SOLE coupling surface between agents (Decision #23a). Stable core + open
    extension. Changing collaboration mode = add to `extra` or subclass — never edit core,
    never edit kernel. Mirrors #9: variability converges to ONE place."""
    model_config = {"extra": "forbid"}        # core is closed; extension goes in `extra`

    # --- frozen core: control semantics (every pattern needs these) ---
    schema_version: int = 1                   # forward-compat: receiver checks/migrates
    from_agent: str
    control: Literal["returnable", "one_way"] # the ONLY two built-in modes (#23)
    objective: str                            # what B must accomplish (Anthropic: required)
    output_format: str = ""                   # expected result shape (Anthropic: required)

    # --- references, not bodies (token hygiene, artifact pattern) ---
    context_refs: list[str] = Field(default_factory=list)   # session/trace ids B may read
    artifacts: list["ArtifactRef"] = Field(default_factory=list)  # external-store handles

    # --- the extensibility seam: mode-specific fields live here ---
    extra: dict[str, "JsonValue"] = Field(default_factory=dict)  # swarm/peer/custom modes

class ArtifactRef(BaseModel):
    kind: Literal["file", "trace", "memory", "session"]
    ref: str                                   # path / id — NOT the body
    summary: str = ""                          # one-line, for the model to decide to fetch

class HandoffResult(BaseModel):
    """What a returnable worker hands back. Mirrors the payload's reference discipline."""
    agent_id: str
    status: Literal["ok", "error", "budget_exhausted", "incomplete"]
    result_payload: "ResultBody"               # condensed findings, NOT the worker's history
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    spent: int                                 # budget this worker consumed (observability)

class ResultBody(BaseModel):
    model_config = {"extra": "forbid"}
    summary: str                               # condensed result (Anthropic: condense before return)
    extra: dict[str, "JsonValue"] = Field(default_factory=dict)
```

### 3. Why `extra` over "just add fields later"

- **Kernel stability:** `spawn()`/loop/budget depend only on the *core* fields
  (`control`, `from_agent`, refs). They never read `extra`. So a new mode that needs
  `priority`/`deadline`/`peer_ring` adds them to `extra` and the kernel is untouched —
  literally the #9 "adding an event touches 2 places" philosophy applied to payloads.
- **Validation without rigidity:** the core is `extra="forbid"` (typos in control fields
  crash loud, per Alfred's config discipline); the open map is explicitly the one place
  unknown keys are allowed. The receiver validates `extra` against its *own* expectation
  (a swarm plugin defines `SwarmExtra(BaseModel)` and parses `payload.extra` itself).
- **Subclass escape hatch:** a plugin may register `class PeerPayload(HandoffPayload)` with
  real typed fields; `spawn()` accepts `HandoffPayload` (Liskov), so plugins get type safety
  while the kernel sees only the base. Two extensibility tiers: cheap (`extra` dict) and
  typed (subclass), neither touching Ring 1.
- **`schema_version`:** future breaking changes to the core itself are migratable — the
  receiver branches on version rather than failing silently (Fail-Loud).

### Comparison to the two industry surfaces

| | OpenAI handoff | Anthropic subagent | Alfred `HandoffPayload` |
|---|---|---|---|
| structured payload | `input_type` (Pydantic) | task obj: objective/output-format/tools/boundaries | core fields + `extra` |
| history transfer | `input_filter`/`HandoffInputData` | fresh context, no history | `context_refs` (opt-in, references) |
| result return | n/a (one-way) | condensed findings / artifact refs | `HandoffResult.result_payload` + artifacts |
| extensibility | new `input_type` per handoff | freeform prompt | versioned core + open `extra`/subclass |

Alfred's `objective`+`output_format` are lifted verbatim from Anthropic's finding that vague
task descriptions cause subagents to duplicate or misread work. `context_refs` is the
typed, opt-in analogue of OpenAI's `input_filter` (default = transfer **nothing** but the
payload — isolation by default, §isolation layer 1).

---

## Control mechanics

The two patterns differ in exactly one axis: **does control return to the parent?**

### Returnable (orchestrator-worker)

```
parent turn ──reserve(1)──▶ spawn(worker, payload, control=returnable)
   │                              │
   │                         emit handoff event
   │                         run_turn(worker_ctx)  ◀── isolated loop, shared budget
   │                              │  worker spends from shared pool under its own ledger
   │                         write_transfer (trace)
   │   ◀── HandoffResult ─────────┘
   ▼
append result_payload as tool result → parent CONTINUES its turn
```

- Parent **awaits** the worker (`await spawner.spawn(...)`), result appended to parent
  history as the `spawn_subagent` tool result. Parent retains control throughout.
- **Parallel workers:** N `spawn_subagent` calls in one assistant turn →
  `asyncio.gather` → concurrent isolated loops, one shared budget (reserve-before-dispatch
  makes overshoot impossible, see kernel:loop-budget H1). Anthropic's 3-5 parallel pattern.
- **Puppeteer** is just orchestrator-worker where the orchestrator's prompt makes it
  actively decompose + sequence + re-spawn based on results. No new mechanism — it "lives
  here" (Decision #23).
- **Failure:** a worker error returns `HandoffResult(status="error", ...)` as a normal tool
  result (spec §7: tool faults feed back, never crash). The orchestrator decides whether to
  retry/re-spawn. Budget already debited via the worker's ledger (no refund for failed work,
  per loop-budget refund rules — failure consumed real tokens).

### One-way (sequential handoff)

```
agent A turn ──▶ handoff_to(B, payload, control=one_way)
   │                  │
   │             emit handoff event
   │             write_transfer (trace)   parent_trace_id = A
   ▼
A's turn ENDS (no result appended). Host starts B's turn from payload.
A never resumes. B owns the conversation.
```

- The `handoff_to` tool returns a `Transfer` sentinel; the loop treats it like the goal
  subsystem's self-continue signal — **ends A's turn**, hands the payload to the host, which
  starts B. No `await`-and-append; control genuinely transfers.
- B is constructed from `AgentSpec` exactly like a worker (same `spawn` factory) — the only
  difference is the loop's post-dispatch branch (append-and-continue vs end-and-transfer).
- Matches OpenAI: "a one-way transfer… execution immediately starts on the new agent."
  Use when B should *own the next response*, not merely help (the agents-as-tools vs
  handoffs distinction).

### The control field is the switch

Both paths read one field: `payload.control`. The loop's post-dispatch logic is a 2-branch
match. Adding a third mode later (e.g. "returnable-with-checkpoint") = add a `control`
literal + one branch — converged surface, no kernel reshape (#23a).

---

## Isolation enforcement

Decision #23a names four isolation layers and says they're change-prone. Each is enforced
at a **specific construction site**, not by convention. This is `_build_isolated_ctx`.

### Layer 1 — context isolation (independent history + budget ledger)

The worker gets a **fresh `TurnCtx`** with its own empty `history` and its own `agent_id`.
It does NOT receive the parent's message list. The only thing that crosses is the payload.

```python
def _build_isolated_ctx(self, spec, payload, child_id, parent_trace_id) -> TurnCtx:
    child_history = MessageHistory()                       # EMPTY — no parent context (#23a-1)
    child_history.seed_system(spec.instructions)          # child's own frozen prefix
    child_history.seed_user(payload.objective)            # only the payload crosses
    return TurnCtx(
        agent_id=child_id,
        history=child_history,                             # independent (layer 1)
        budget=self._budget,                               # SAME instance, own ledger (layer 1)
        tools=self._scope_tools(spec.tool_names),          # scoped, not inherited (layer 2)
        trace_id=self._trace.new(parent_trace_id, spec.role),  # own trajectory (layer 3)
        provider=self._reg.models.resolve(spec.model),
        bus=self._bus)
```

- **History:** separate `MessageHistory` object. Anthropic: "each subagent runs in its own
  context window and never sees what the others are doing." The parent's mid-turn context
  is never visible; only `payload` + (opt-in) `context_refs` the worker may *choose* to
  fetch from the session/trace store.
- **Budget ledger:** the **same `IterationBudget` instance** is injected (shared cap), but
  the worker uses its own `agent_id` key → separate `_spent[agent_id]` accounting. One pool,
  many recorded spenders (kernel:loop-budget two-counter SSoT). "Shared total cap, separate
  accounting" (#23a) is literally `budget` shared + `agent_id` distinct.

### Layer 2 — tool-permission isolation (scoping per agent)

Workers do **not** auto-inherit the parent toolset (#23a, and Anthropic: "a research
subagent can be read-only; a code-review subagent gets Bash+Grep but no Edit/Write").
Enforced by building the child's tool dict from an **explicit allowlist** in `AgentSpec`.

```python
def _scope_tools(self, tool_names: list[str]) -> dict[str, Tool]:
    scoped = {}
    for n in tool_names:
        if n not in self._reg.tools:
            raise ToolScopeError(f"agent requested unregistered tool {n!r}")  # fail loud
        scoped[n] = self._reg.tools[n]
    return scoped                          # child sees ONLY these; default = empty, not parent's
```

- Default is **deny** (empty grant), the opposite of inheritance. The orchestrator must name
  each tool it delegates. A worker requesting a tool outside its grant simply can't see it
  (not in its registry view) — structurally impossible, not policy-checked at call time.
- `spawn_subagent` itself can be withheld → bounds recursion depth (a worker without the
  spawn tool can't spawn). Depth limiting is thus just tool-scoping, no special counter.
- Mirrors OpenAI's `is_enabled` + per-agent `tools` list and Claude Code's per-subagent
  tool allowlist/denylist.

### Layer 3 — state isolation (independent session/trace; only payload crosses)

- Worker writes its **own trace trajectory** (`trace_id` distinct, `parent_trace_id` links
  the chain, `agent_role` tags it) — store-trace already has these columns. Evolve can scope
  replay sets by `agent_role`.
- Worker has **no shared mutable state** with the parent. The only data structures it holds
  are its own (history, ledger key, trace id). This is what avoids cybernetics #4
  multi-writer drift (#23a): there is no shared object both agents write — they communicate
  by **value** (the payload) and by **reference to immutable store records** (`context_refs`,
  `artifacts`), never by shared mutable handle.
- The **transfer record** (`handoff_payload` column) is the persisted A→B contract instance,
  written once at spawn → durable audit + evolve material.

### Layer 4 — communication ONLY via payload + handoff event (the sole coupling surface)

Enforced negatively: the worker's `TurnCtx` simply **contains no channel back to the parent**
except its return value. There is no callback, no shared queue, no parent reference in the
child ctx. The `handoff` event is the only broadcast (observers/trace), and `HandoffPayload`/
`HandoffResult` are the only data that cross. Because the coupling is structurally narrow,
"future mode changes touch only it" (#23a) is enforced by construction, not discipline.

### Budget tie-in (explicit, per the prompt)

Separate ledgers, shared cap is realized as: **one `IterationBudget` object** (the cap +
`total_remaining` pool) shared by reference into every child ctx; **per-`agent_id` `_spent`
ledgers** for accounting and per-agent `budget_warning`. Spawning costs the *parent* 1
reservation; the worker's own tool calls debit under the worker's ledger. The two-counter
invariant `total_remaining + Σ ledgers == total_cap` holds across all agents (kernel:loop-budget).
Concurrent workers can't overshoot because `reserve()` is a synchronous await-free critical
section (H1 fix). Handoff adds **no new budget mechanism** — it just injects the same object
with new `agent_id`s.

### Worktree file isolation (deferred, declared)

Filesystem-level isolation (each agent in its own git worktree so parallel edits don't
collide) is **TODO** (Decision #30, "maybe later"). MVP covers context/tool/state/comms.
This is a *strengthening* of layer 3, not a gap in it — declared so it reads as deliberate.

---

## Industry refs with URLs

- **OpenAI Agents SDK — Handoffs.** `transfer_to_<agent>` tool naming; `handoff()` params
  (`tool_name_override`, `on_handoff`, `input_type`, `input_filter`, `is_enabled`);
  `input_type` = Pydantic structured payload (`reason`/`priority`/`summary`);
  `input_filter`/`HandoffInputData` controls transferred history; one-way "execution
  immediately starts on the new agent." → Alfred's sequential handoff + payload.
  https://openai.github.io/openai-agents-python/handoffs/
- **OpenAI Agents SDK — Orchestration (agents-as-tools vs handoffs).** Agents-as-tools =
  orchestrator retains control, consults specialists, combines responses (= Alfred
  returnable orchestrator-worker). Handoffs = one-way, specialist owns next response (=
  Alfred sequential). https://openai.github.io/openai-agents-python/multi_agent/ ·
  https://developers.openai.com/api/docs/guides/agents/orchestration
- **Anthropic — How we built our multi-agent research system.** Orchestrator-worker; lead
  spawns 3-5 subagents in parallel, each own context window, never see each other; task =
  objective + output format + tool guidance + boundaries; vague tasks cause duplication;
  **artifact pattern** (store work externally, pass lightweight refs); condensed findings
  return; effort-scaling heuristics; ~15× tokens. → Alfred orchestrator-worker, payload
  core fields, `artifacts`/`context_refs` reference discipline.
  https://www.anthropic.com/engineering/multi-agent-research-system
- **Anthropic — Claude Code sub-agents.** Each subagent = isolated context window, own
  system prompt, own tool allowlist/denylist, own permission mode; does NOT inherit parent
  context; parent receives only final output; `isolation: worktree` mode. → Alfred isolation
  layers 1-3 + tool-scoping + worktree TODO.
  https://code.claude.com/docs/en/sub-agents
- **Anthropic — Building Effective AI Agents.** Orchestrator-workers as a named pattern
  (~70% production convergence cited in #23).
  https://www.anthropic.com/research/building-effective-agents
- **OpenAI — A practical guide to building agents (manager vs decentralized patterns).**
  https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/

---

## Open questions

1. **Returnable result size cap.** `HandoffResult.result_payload.summary` should be bounded
   so a verbose worker can't bloat the orchestrator (Anthropic's whole reason for the
   artifact pattern). Hard cap + force-to-artifact above threshold? Where (tool layer vs
   payload validator)? Leaning: payload validator warns, tool layer truncates-to-artifact.
2. **One-way handoff host loop.** Sequential handoff ends A's turn and the *host* starts B.
   In a daemon (long-lived), is B a new session or a continuation? Ties to Eng H3 (daemon
   session boundary). Proposal: one-way handoff = new session with `parent_trace_id` link,
   matching cron's fresh-session model.
3. **Handoff loop / cycle guard.** A→B→A (one-way) could ping-pong. Tool-scoping bounds
   returnable recursion (layer 2), but one-way chains have no natural depth bound. Add a
   `hop_count` to the payload core + a cap? Or rely on the shared budget as the only
   backstop? (Budget is the safety net but late; an explicit hop cap fails louder.)
4. **`context_refs` read authority.** A worker can fetch session/trace records by id — but
   should isolation forbid reading the *parent's* private session? Today refs are opt-in by
   the parent (it only puts ids it wants shared). Confirm: the parent curates refs; the
   worker cannot enumerate the store. Needs a store-layer scoping check (cross-ref store-session).
5. **Swarm/peer via `extra` — is the seam really enough?** Decision #23 leaves swarm as a
   plugin. Validate that peer messaging (N agents, no central orchestrator) is expressible
   as repeated one-way handoffs carrying `extra={"peer_round": k}` without a shared bus —
   if peers need true shared mutable state, that violates layer 3 and needs a separate
   design (out of MVP, but confirm the seam doesn't silently mislead).
6. **Schema migration ownership.** `schema_version` bumps: who migrates — sender, receiver,
   or a central migrator? Leaning receiver-side `migrate(payload)` (forward-compat), but a
   shared migrator avoids per-receiver drift. Decide before the first version bump.
