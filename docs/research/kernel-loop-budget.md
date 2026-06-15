# Ring-1 Kernel — Agent Loop + Iteration Budget

Module research for the Alfred design spec (§3, Decision #4b, #23a). Resolves Eng-review **H1**.
Date: 2026-06-15. Scope: the async agent loop and the shared iteration budget across
parent + concurrent subagents/handoff workers.

---

## Module scope

Ring-1, not pluggable. Two coupled concerns:

1. **loop** — `input → assemble prompt → ModelProvider.complete() → parse → dispatch tool → repeat`,
   capped by an **iteration budget** (tool calls per turn). Knows only one
   `ModelProvider.complete()`; unaware of fusion/Ring-3 (spec §3).
2. **iteration budget** — one **total cap** shared by the parent loop and any concurrent
   sub-loops (subagent S1, handoff workers #23), with **separate per-agent accounting**
   (ledgers) and **refund** for `execute_code`-class tools. This is the contended state
   the Eng review flagged (H1).

Out of scope (other modules): event-bus internals (kernel:event-bus), context assembly +
cache (kernel:context+cache), provider layer, trace store, subagent/handoff subsystem
mechanics. This doc only owns: loop control flow, the budget API, and how
`budget_warning`/`budget_exhausted` fire under concurrency.

---

## Recommended design

### 1. Core loop stays tiny (~100 lines)

Follow mini-SWE-agent's discipline: a **linear append-only message history**, one
completion → one (or more) tool dispatch → append result → repeat. No control logic
hiding in the loop beyond: budget gate, parse, dispatch, emit events. Everything else is a
Ring-2/3 subscriber. Keep the loop a flat `async def`, not a class hierarchy.

Two deviations from mini-SWE-agent, both required by Alfred's design:
- mini-SWE-agent uses **bash-only, no tool-calling API**. Alfred uses the provider's
  native tool-calling (Decision #28, LiteLLM normalizes it) because we need structured
  `pre_tool`/`post_tool` events and an mcp/tools registry (SSoT one dispatch path, #21).
- A turn may emit **multiple tool calls** (provider returns N tool_use blocks). The budget
  is charged per dispatched tool call, not per LLM round-trip.

**Tool-result-feedback-on-exception (never crash the loop)** — spec §7. Any exception from
a tool handler is caught at the dispatch site, converted to a tool-result message
(`is_error=True`), appended, and fed back to the model. The loop only ever crashes on
*kernel* faults (provider auth failure, corrupt config) per Fail-Loud — never on tool
faults. This is the single most important robustness rule and it lives in `_dispatch`, not
scattered.

```python
async def run_turn(ctx: TurnCtx) -> TurnResult:
    while True:
        await ctx.bus.emit(TurnStart(turn_id=ctx.turn_id))           # event policy decides dispatch
        resp = await ctx.provider.complete(ctx.assemble_messages())   # the ONLY await-heavy call
        ctx.history.append(resp.message)
        calls = resp.tool_calls
        if not calls:
            await ctx.bus.emit(TurnEnd(turn_id=ctx.turn_id))
            return TurnResult(message=resp.message)
        for call in calls:
            grant = ctx.budget.reserve(ctx.agent_id, n=1)            # SYNC, atomic — see H1
            if grant is None:
                ctx.history.append(tool_result(call, BUDGET_EXHAUSTED_MSG, is_error=True))
                await ctx.bus.emit(BudgetExhausted(agent_id=ctx.agent_id))
                return TurnResult(message=resp.message, stopped="budget")
            result = await _dispatch(ctx, call)                       # never raises to loop
            if grant.refundable and result.refund_ok:
                ctx.budget.refund(grant)                             # SYNC, atomic
            ctx.history.append(tool_result(call, result))

async def _dispatch(ctx, call) -> ToolResult:
    try:
        await ctx.bus.emit(PreTool(...))          # blockable: subscriber may raise ToolVeto
        out = await ctx.tools[call.name].handler(**call.args)
        res = ToolResult(ok=True, body=out)
    except ToolVeto as e:
        # veto is a control signal, not a tool fault and not a generic exception.
        res = ToolResult(ok=False, body=str(e), is_error=True, status="vetoed")
    except Exception as e:                         # tool fault → feed back, do NOT crash
        res = ToolResult(ok=False, body=f"{type(e).__name__}: {e}", is_error=True)
    await ctx.bus.emit(PostTool(...))
    return res
```

### 2. Budget is a single-owner object, mutated only by synchronous methods

The whole H1 fix rests on one asyncio fact:

> **In single-threaded asyncio, a coroutine yields control only at an `await`. Code with no
> `await` between read and write is atomic w.r.t. all other tasks — no lock needed.**

So make `reserve`/`refund` **plain synchronous methods with zero `await` inside**. The
read-modify-write (`if remaining >= n: remaining -= n`) cannot be interleaved, so two
concurrent workers can never both pass the check and overshoot. The race in the spec exists
only if the check and the decrement straddle an `await` (e.g. "check remaining, then
`await dispatch`, then decrement"). The fix is **reserve-before-dispatch**: charge the
budget *before* the awaited work, refund after if applicable.

No `asyncio.Lock` is needed for the in-memory single-process budget. (A lock would be
required only if `reserve` itself had to `await` — it must not. If a future multi-process
daemon shares a budget, that moves to the store layer with its own transaction, out of
Ring-1 scope.)

### 3. Reserve-before-dispatch, refund-after (the `execute_code` case)

- **reserve** atomically debits N from the shared total and credits the agent's ledger,
  returning a `Grant` handle (or `None` if insufficient). Done *before* the awaited tool runs.
- **refund** atomically credits N back to the shared total and debits the agent's ledger,
  using the `Grant` handle. Idempotent (a `Grant` can be refunded at most once).
- **`execute_code`-class refund semantics:** some tools (run code, run shell, sub-queries)
  are declared `refundable=True` in the tools registry. They still **reserve up-front**
  (so a burst of concurrent refundable calls can't overshoot), but on a clean/cheap result
  the grant is refunded, so they don't permanently consume budget. Refund is keyed to the
  *issuing agent's* ledger via the `Grant`, so cross-agent accounting stays correct even
  when a subagent issued the call. "Refundable" is a property of the **tool**, not the agent.

### 4. Two-counter invariant (the SSoT for budget)

The budget owns exactly two kinds of number, and they must always reconcile:

- `total_remaining` — the one shared pool (the cap is `total_cap`).
- `ledger[agent_id]` — per-agent **spent** count (for accounting/observability + per-agent
  events), never an independent pool.

Invariant: `total_remaining + sum(ledger.values()) == total_cap` at every point outside a
sync method body. This is checkable in a test and is the SSoT (one pool, many recorded
spenders — matches CLAUDE.md SSoT: same storage, many writers).

---

## Interface sketch

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class Grant:
    agent_id: str
    n: int
    refundable: bool
    _id: int                 # unique; refund() dedups on this

class IterationBudget:
    """Single owner of the shared iteration cap. All mutators are SYNCHRONOUS and
    contain NO `await` — that is what makes reserve/refund atomic under asyncio
    (cooperative scheduling can't interleave a no-await critical section)."""

    def __init__(self, total_cap: int, warn_at_frac: float = 0.8):
        self._cap = total_cap
        self._remaining = total_cap
        self._spent: dict[str, int] = {}
        self._warn_threshold = int(total_cap * warn_at_frac)
        self._warned = False
        self._seq = 0
        self._on_warning = None      # injected callbacks (loop wires to event-bus)
        self._on_exhausted = None

    # --- atomic: no await inside ---
    def reserve(self, agent_id: str, n: int = 1, *, refundable: bool = False) -> Grant | None:
        if self._remaining < n:
            self._fire_exhausted(agent_id)        # sync emit — see §events below
            return None
        self._remaining -= n
        self._spent[agent_id] = self._spent.get(agent_id, 0) + n
        self._seq += 1
        self._maybe_fire_warning(agent_id)        # crosses threshold exactly once
        return Grant(agent_id, n, refundable, self._seq)

    def refund(self, grant: Grant) -> None:
        # idempotent: a Grant refunds at most once
        if grant._id in self._refunded:           # set[int]
            return
        self._refunded.add(grant._id)
        self._remaining += grant.n
        self._spent[grant.agent_id] -= grant.n
        self._warned = self._remaining <= self._warn_threshold  # re-arm if refund lifts us back

    # --- read-only views ---
    @property
    def remaining(self) -> int: return self._remaining
    def spent_by(self, agent_id: str) -> int: return self._spent.get(agent_id, 0)
    def reconciles(self) -> bool:
        return self._remaining + sum(self._spent.values()) == self._cap
```

Sub-loops (subagent/handoff workers) receive **the same `IterationBudget` instance** by
constructor injection (Decision #23a: "shared total cap, separate accounting"). They pass
their own `agent_id`, so the shared pool is one object, the ledgers are distinct keys —
exactly the SSoT shape.

```python
# parent
budget = IterationBudget(total_cap=cfg.max_tool_calls)
parent_ctx = TurnCtx(agent_id="root", budget=budget, ...)

# spawning a worker (subagent/handoff) — SAME budget, new agent_id
worker_ctx = TurnCtx(agent_id=f"worker:{wid}", budget=budget, ...)
await asyncio.gather(run_turn(parent_ctx), run_turn(worker_ctx))   # concurrent, one pool
```

---

## Gap answers

### H1 — atomic decrement under concurrent subagents (the core gap)

**Diagnosis.** The race the Eng review describes ("two workers both pass `remaining>0`,
overshoot") can only happen if the **check and the decrement are separated by an `await`**.
The classic broken shape:

```python
# BROKEN: check-then-await-then-act — yields between read and write
if budget.remaining > 0:          # task A and task B both see 1
    await tool.run()              # <-- both suspend here, scheduler interleaves
    budget.remaining -= 1         # both decrement → overshoot to -1
```

**Fix: reserve-before-dispatch with a synchronous, await-free critical section.**
Because asyncio is single-threaded and cooperative, `reserve()` (read + compare + write,
no `await`) runs to completion before any other task can touch `_remaining`. Two workers
calling `reserve()` are serialized by the event loop itself; the second sees the
already-decremented value and gets `None`. **No `asyncio.Lock` required** — and adding one
would be a smell, because the only reason you'd need it is if you wrongly put an `await`
inside the critical section.

- **reserve-before-dispatch vs check-after:** choose **reserve-before**. Charge the budget
  *before* awaiting the tool. "Check-after" (run the tool, then decrement) is exactly the
  broken shape — it lets N concurrent dispatches all start before any decrements land.
  Reserve-before makes overshoot structurally impossible: the pool is debited before the
  awaited work, so the (N+1)th concurrent reserver is rejected synchronously.
- **single owner:** one `IterationBudget` instance, injected into every (sub-)loop. The
  pool (`_remaining`) is the SSoT; per-agent `_spent` are derived records, not pools.
- **why not a Lock/Semaphore around an async section:** a `Semaphore(total_cap)` *could*
  cap concurrent in-flight calls, but it conflates "max concurrency" with "total budget"
  and can't express refund or per-agent ledgers cleanly. The sync-method approach is
  simpler (Delete>Add) and directly models the two-counter invariant.

### `execute_code`-class refund across agents

`refundable` is a **tool-registry property**. Flow, fully concurrency-safe:

1. Worker `w` calls `reserve("worker:w", n=1, refundable=True)` → debits the shared pool
   up-front (so a burst of refundable calls from many workers still can't overshoot).
2. `await tool.handler(...)` runs (the await is *outside* the critical section).
3. On a refund-eligible outcome, worker calls `refund(grant)`. Because the `Grant` carries
   `agent_id`, the credit lands back in the shared pool and the debit is removed from
   **that worker's** ledger — cross-agent accounting stays exact even though refund happens
   on a different task than some other agent's concurrent reserve. `refund` is also
   await-free and idempotent, so concurrent refund + reserve from sibling tasks never race
   or double-count.

What counts as "refund-eligible" is the tool's call: e.g. `execute_code` that returned
cleanly and cheaply refunds; one that errored or looped does not (it consumed real work).
The loop passes `result.refund_ok` through; the kernel does not hard-code the policy.

### H2 / async-subscriber note (cross-ref only)

`budget_warning`/`budget_exhausted` are decided inside `reserve()` (sync context), but the
budget object does not call async bus APIs from that no-await critical section. Instead,
`reserve()` records pending notification events (or returns them with the `Grant`/failure),
and the loop publishes them immediately after the critical section. They are background
notification events, so a slow subscriber can't stall reservation. `pre_tool` stays
blockable/vetoable and is awaited by `_dispatch`.

---

## Events fire consistently under concurrency

Both budget events originate from the **single** `IterationBudget` owner, so there is one
emit point per event-type — no per-agent duplication or drift.

- **`budget_warning`** — fired **exactly once** when cumulative spend first crosses
  `warn_at_frac` of the cap. A `_warned` flag (flipped inside the await-free `reserve`)
  guarantees once-only even when many workers reserve concurrently: whichever reserve
  crosses the line flips the flag atomically; the rest see it already set. (Refund that
  lifts remaining back above the threshold re-arms `_warned`, so warning can fire again if
  spend re-crosses — correct hysteresis, not a stuck flag.)
- **`budget_exhausted`** — fired when a `reserve` is rejected. It may be attempted by
  several workers (each failing reserve fires it), so subscribers must treat it as
  **idempotent / level-triggered** (it signals "pool is empty now", not "this is the
  first failure"). The payload carries `agent_id` of the rejected requester so the UI can
  show *who* hit the wall. The pool being a single owner means there's no inconsistency:
  every rejection reflects the same true `_remaining == 0`(or `< n`) state.
- **Why this is consistent:** because the flag-flip and the emit decision both happen
  inside the await-free critical section, the event ordering is total and matches the true
  budget timeline. There is no window where two tasks each "think" they crossed the
  threshold first. Publishing itself happens after the await-free `reserve()` critical
  section; the bus then supervises background subscribers without blocking the reserver.

Concrete payloads (pydantic, per spec §3.1 naming):

```python
class BudgetWarning(Event):   name = "budget_warning"
    agent_id: str; remaining: int; cap: int
class BudgetExhausted(Event): name = "budget_exhausted"
    agent_id: str; cap: int
```

---

## Industry refs

- mini-SWE-agent — 100-line loop, linear append-only history, tool-result-fed-back,
  subprocess-per-action. The size discipline Alfred's loop targets.
  https://github.com/SWE-agent/mini-swe-agent
- mini-SWE-agent architecture deep-dive (loop = stateless orchestration, stdout/returncode
  appended to context). https://deepwiki.com/SWE-agent/mini-swe-agent
- Hermes Agent — Agent Loop Internals (iteration budget; "subagents get independent
  budgets, total across parent + subagents can exceed parent cap" — Alfred *diverges*: one
  shared cap, separate ledgers, to bound true total cost).
  https://hermes-agent.nousresearch.com/docs/developer-guide/agent-loop
- Python asyncio Synchronization Primitives — Lock/Semaphore semantics; basis for the
  "no-await critical section is atomic" argument.
  https://docs.python.org/3/library/asyncio-sync.html
- Inngest — "What Python's asyncio primitives get wrong about shared state" — lost-update /
  missed-transition pitfalls when state changes across await; reinforces keeping mutation
  out of awaited regions. https://www.inngest.com/blog/no-lost-updates-python-asyncio
- Token/iteration budget *enforcement* (block before the call, not alert-after) — pattern
  Alfred's reserve-before-dispatch implements at the iteration granularity.
  https://waxell.ai/blog/ai-agent-token-budget-enforcement ·
  https://github.com/pykul/tokencap
- Agentic Loops 2026 guide (ReAct → loop engineering) — loop-design context.
  https://datasciencedojo.com/blog/agentic-loops-explained-from-react-to-loop-engineering-2026-guide/

---

## Open questions

1. **Budget granularity: tool-calls vs LLM-round-trips vs tokens.** Spec says "tool calls
   per turn". A turn with N parallel tool_use blocks charges N. Should an LLM round-trip
   with zero tool calls also cost 1 (to bound pure-reasoning loops)? Lean: no — goal
   self-continuation (#19) has its own cap; keep iteration budget = tool-call count.
2. **Per-agent sub-caps.** Current design = one shared pool + ledgers (any agent can drain
   it). Do we also want an optional per-worker ceiling (a worker may spend at most M of the
   shared pool) to stop one runaway subagent starving siblings? Cheap to add to `reserve`
   (`if self._spent[agent_id] + n > self._sub_cap: return None`). Defer until an e2e shows
   starvation (YAGNI), but the hook is identified.
3. **Refund policy ownership.** Who decides `result.refund_ok` — the tool handler, or a
   registry-level policy? Lean: the handler returns a cost hint, registry marks
   `refundable`; kernel just multiplies. Needs the tools-registry module to agree on the
   `ToolResult` shape.
4. **Budget reset boundary.** Cap is "per turn" for the parent, but subagents/handoff span
   turns. Confirm the shared pool resets at **parent turn_start** and that in-flight
   workers from a prior turn are joined/cancelled before reset (else a stale Grant refunds
   into a fresh pool and breaks the reconcile invariant). This couples to the
   subagent/handoff lifecycle module — flag for cross-module alignment.
5. **L9 negative-path e2e.** Add a row: spawn K concurrent workers with `total_cap = K-1`,
   assert exactly `K-1` tool calls execute, one gets `budget_exhausted`, and
   `budget.reconciles()` holds at the end. This is the machine-checkable proof that H1 is
   fixed.
