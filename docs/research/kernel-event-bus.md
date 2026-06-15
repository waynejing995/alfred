# Module Research: Ring-1 Kernel — Event Bus

Date: 2026-06-15
Module: `agentkit/kernel/events`
Resolves: Decisions #5, #7, #9, #15, #25; Eng review **H2** (async subscriber error policy).
Status: research → back-fill into spec §3.1.

---

## Module scope

Single-process, single-asyncio-loop pub/sub bus. The ONE event stream feeding three
consumers (#3/#25): in-process CLI, `agentkit-server` SSE, and Ring-3 hooks. NOT a
message broker (no Redis/queue/cluster — YAGNI per #5).

In scope:
- `emit` / `on` / `off`, sync + async subscribers on the same bus.
- Wildcard subscription `on("*")`, `on("tool.*")` (#9).
- Two dispatch modes: **sync/blockable** (`pre_tool`, veto-by-raise) vs
  **async/background-trigger** (`turn_end`/`idle`/`session_end`) (#7).
- Self-describing pydantic events, generic `{type, payload}` serialization, no central
  enum (#9).
- **H2: async subscriber error isolation + visibility** — the critical gap.
- Async-generator outlet `bus.stream()` for SSE / `on("*")` UI consumer (#15/#25).

Out of scope: cross-process delivery (that is the SSE wire format, downstream), persistence
/ WAL, ordering guarantees beyond per-emit registration order.

---

## Recommended design

### Core decisions

1. **Registry = `dict[pattern -> list[Subscription]]`.** Pattern is either an exact name
   (`"turn_end"`), a namespace glob (`"tool.*"`), or `"*"`. Match at emit time by walking
   three buckets: exact, prefix-glob, universal. O(1) exact + O(#glob-patterns) — fine for
   a kernel with <30 event types.

2. **One async `emit`, two dispatch policies, selected by the EVENT, not the call site.** The
   event class declares `blockable: ClassVar[bool]`. `pre_tool` is blockable=True →
   awaited inline, first raise propagates (veto). Background events blockable=False →
   isolated supervised fan-out that cannot hang the emitter (see H2). This keeps the loop call sites uniform: always
   `await bus.emit(ev)`; the event type decides semantics (SSoT — dispatch policy lives
   with the event definition, not scattered at emit sites).

3. **Sync subscribers are wrapped, not special-cased.** A sync callable is invoked
   directly (no thread offload — kernel subscribers are non-blocking by contract); an async
   callable is awaited. Same registration path (`on`). Mirrors bubus / aiopubsub 2026
   convention (one `on`, runtime-detect coroutine fn).

4. **Generic serialization, zero per-event code.** `serialize(ev) -> {"type": ev.name,
   "payload": ev.model_dump(mode="json")}`. `mode="json"` so datetimes/enums/UUIDs are
   wire-ready for SSE in one step. No per-event case anywhere.

5. **Namespace validation at registration AND at event-class definition.** Kernel events =
   bare name (no dot). Plugin events = `prefix.suffix`, prefix owned by emitter. Registering
   an emitter against a reserved/foreign prefix → raise at registration (fail loud, #9).

### The "add a kernel event = 2 places" invariant (#9) — verified

To add e.g. `budget_refunded`:
1. **event-defs file**: add the pydantic class (`class BudgetRefunded(Event): name = ...`).
2. **one emit site**: `await bus.emit(BudgetRefunded(...))` in the loop.

NOT touched: no central enum (there is none), no serialization layer (generic
`model_dump`), no UI (subscribed via `on("*")`/`on("budget.*")`). A regression test asserts
the git diff of an added event touches exactly these 2 files (#25, E2E: none row).
**Invariant holds.** ✔

---

## Interface sketch

```python
# agentkit/kernel/events/base.py
from __future__ import annotations
import re
from typing import ClassVar
from pydantic import BaseModel, model_validator

_BARE = re.compile(r"^[a-z][a-z0-9_]*$")          # kernel: turn_end
_NS   = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_.]+$")  # plugin: dream.consolidated

class Event(BaseModel):
    """Self-describing kernel/plugin event. No central enum (#9)."""
    name: ClassVar[str]                 # set per subclass; the wire `type`
    blockable: ClassVar[bool] = False   # True -> sync veto dispatch (#7)
    namespace: ClassVar[str] = ""       # "" = kernel(bare); else owning plugin prefix

    model_config = {"frozen": True, "extra": "forbid"}  # immutable payload, schema-first

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)
        nm = getattr(cls, "name", None)
        if nm is None:
            return  # abstract intermediate
        if cls.namespace:                       # plugin event
            if not _NS.match(nm) or not nm.startswith(cls.namespace + "."):
                raise ValueError(
                    f"plugin event {cls.__name__}: name {nm!r} must be "
                    f"'{cls.namespace}.<suffix>'")
        elif not _BARE.match(nm):               # kernel event
            raise ValueError(f"kernel event {cls.__name__}: name {nm!r} must be bare")

def serialize(ev: Event) -> dict:
    return {"type": ev.name, "payload": ev.model_dump(mode="json")}
```

```python
# agentkit/kernel/events/defs.py  -- THE event-defs file (edit #1 to add an event)
class PreTool(Event):
    name = "pre_tool"; blockable = True
    session_id: str; turn_id: str; tool_name: str; args_ref: str  # refs only (#7)

class TurnEnd(Event):
    name = "turn_end"           # blockable defaults False -> background fan-out
    session_id: str; turn_id: str

# ... session_start, turn_start, post_tool, session_end, idle, tick, job_due,
#     skill_used, budget_warning, budget_exhausted, handoff
```

```python
# agentkit/kernel/events/bus.py
import asyncio, inspect, fnmatch
from collections.abc import AsyncIterator, Callable
from loguru import logger
from .base import Event, serialize

RESERVED = {"", "kernel", "alfred", "sys"}   # foreign-namespace guard (#9)

class EventBus:
    def __init__(self, *, background_timeout_s: float = 30.0) -> None:
        self._subs: dict[str, list[Callable]] = {}
        self._sinks: list[asyncio.Queue] = []     # stream() outlets (#15/#25)
        self._tasks: set[asyncio.Task] = set()    # supervised background subscribers
        self._background_timeout_s = background_timeout_s

    # ---- registration -------------------------------------------------
    def on(self, pattern: str, fn: Callable, *, owner_ns: str = "") -> Callable:
        # fail loud: a plugin may only register an EMITTER under its own ns;
        # subscription patterns are open (anyone may listen to anything).
        self._subs.setdefault(pattern, []).append(fn)
        return fn                                  # so it can be passed to off()

    def off(self, pattern: str, fn: Callable) -> None:
        lst = self._subs.get(pattern)
        if lst and fn in lst:
            lst.remove(fn)

    def register_emitter(self, ev_cls: type[Event], owner_ns: str) -> None:
        ns = ev_cls.namespace
        if ns in RESERVED and owner_ns not in ("", "kernel"):
            raise ValueError(f"{owner_ns!r} may not emit reserved-ns event {ev_cls.name!r}")
        if ns and ns != owner_ns:
            raise ValueError(f"{owner_ns!r} may not emit foreign-ns event {ev_cls.name!r}")

    # ---- dispatch -----------------------------------------------------
    def _match(self, name: str) -> list[Callable]:
        out: list[Callable] = []
        for pat, fns in self._subs.items():
            if pat == name or pat == "*" or (pat.endswith(".*") and
                                             name.startswith(pat[:-1])):
                out += fns
        return out

    async def emit(self, ev: Event) -> None:
        for q in self._sinks:                      # feed SSE/UI stream first
            q.put_nowait(serialize(ev))
        fns = self._match(ev.name)
        if ev.blockable:
            await self._emit_blocking(ev, fns)     # pre_tool: veto-by-raise
        else:
            await self._emit_isolated(ev, fns)     # turn_end/idle/... : H2

    async def _emit_blocking(self, ev: Event, fns: list[Callable]) -> None:
        # sequential; FIRST raise propagates to caller (veto). No isolation by design.
        for fn in fns:
            r = fn(ev)
            if inspect.isawaitable(r):
                await r

    async def _emit_isolated(self, ev: Event, fns: list[Callable]) -> None:
        # H2: every subscriber isolated; one failure/hang never kills siblings or the loop.
        async def run(fn):
            try:
                async with asyncio.timeout(self._background_timeout_s):
                    r = fn(ev)
                    if inspect.isawaitable(r):
                        await r
            except Exception as exc:               # noqa: BLE001 -- intentional, see below
                await self._report_subscriber_error(ev, fn, exc)
        for fn in fns:
            task = asyncio.create_task(run(fn))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        # Return after enqueueing tasks. Background events must never stall the emitter.

    async def _report_subscriber_error(self, ev, fn, exc) -> None:
        # FAIL-LOUD: log ERROR + emit a first-class event. NOT swallowed.
        logger.opt(exception=exc).error(
            "subscriber {} failed on {}: {}", getattr(fn, "__qualname__", fn), ev.name, exc)
        err = SubscriberError(source_event=ev.name,
                              handler=getattr(fn, "__qualname__", repr(fn)),
                              error_type=type(exc).__name__, message=str(exc))
        # re-entrancy guard: a failing SubscriberError handler must not recurse forever.
        if ev.name != err.name:
            await self._emit_isolated(err, self._match(err.name))

    # ---- stream outlet (in-process generator == SSE source) ----------
    async def stream(self) -> AsyncIterator[dict]:
        q: asyncio.Queue = asyncio.Queue()
        self._sinks.append(q)
        try:
            while True:
                yield await q.get()                # {"type":..,"payload":..}
        finally:
            self._sinks.remove(q)
```

```python
# the SubscriberError event lives in defs.py with the others (#9 invariant intact)
class SubscriberError(Event):
    name = "subscriber.error"; namespace = ""   # kernel-owned but dotted: see Open Q3
    source_event: str; handler: str; error_type: str; message: str
```

---

## Gap answers (H2)

**Problem.** `turn_end`/`idle`/`session_end` drive ALL Ring-3 loops (distill/dream/
evolve/goal). Two naive policies both fail:
- `await` each subscriber unhandled → first raise crashes the kernel loop (one buggy
  plugin halts the agent). **Bad.**
- fire-and-forget (`asyncio.create_task` + drop) → exception swallowed, logged nowhere,
  silent degradation. **Violates Fail-Loud.**

**Policy (concrete).** For background (`blockable=False`) events ONLY:

1. **Isolate** — wrap each subscriber in its own supervised task with try/except and a
   per-subscriber timeout. `emit()` schedules those tasks and returns after enqueueing; it
   does not await `gather()` for background events, because one hung plugin must not hang
   the kernel loop. We deliberately do NOT use bare fire-and-forget: tasks are tracked in
   `_tasks`, exceptions/timeouts emit `subscriber.error`, and shutdown can drain/cancel the
   tracked set. We also do NOT use `asyncio.TaskGroup` here: TaskGroup cancels siblings on
   first failure (2026 docs) — correct for "all-or-nothing" subtasks, **wrong** for
   independent Ring-3 loops where one plugin's failure must not cancel the others.

2. **Visibility — two channels, both mandatory:**
   - `logger.error(... exception=exc)` with full traceback (operator-visible).
   - `emit(SubscriberError(...))` — a first-class kernel event carrying
     `source_event / handler / error_type / message`. This makes failures observable
     on the SAME bus (CLI/SSE/an alerting Ring-3 subscriber can react). This is the
     Fail-Loud teeth: the failure is a signal on the stream, not a swallowed log line.

3. **Re-entrancy guard.** A handler subscribed to `subscriber.error` (or `*`) that itself
   raises would loop. Guard: when the failing event IS `subscriber.error`, log ERROR but
   do NOT re-emit (the `if ev.name != err.name` check). One hop max.

4. **Sync/blockable path is the opposite by design.** `pre_tool` keeps NO isolation: the
   FIRST raise propagates so a guard subscriber can veto the tool call. Isolation there
   would defeat the veto contract (#7). The two policies are intentional mirror images,
   keyed off `Event.blockable`.

**Net contract:** background subscriber failure or timeout → sibling subscribers unaffected,
kernel loop unaffected, ERROR logged, `subscriber.error` emitted. This satisfies both
"don't crash the loop" and "don't degrade silently" — the exact H2 ask.

**Negative-path e2e (feeds L9).** New row: a deliberately-raising `turn_end` subscriber →
assert (a) the turn still completes, (b) a `subscriber.error` event is observed on the
SSE stream, (c) ERROR line in log. This is user-observable, so it is a real e2e row, not
`E2E: none`.

---

## SSE serialization + in-process consumption (#3/#15/#25) — invariant check

One generic rule, applied at exactly one place:

```python
# agentkit-server: GET /events  -- the ONLY serialization site
async def sse_endpoint(bus: EventBus):
    async for frame in bus.stream():          # frame already == {"type","payload"}
        yield f"event: {frame['type']}\n" \
              f"data: {json.dumps(frame['payload'])}\n\n"
```

```python
# in-process consumer (CLI / replay script / future TUI) -- same stream, no serialization
async for frame in bus.stream():
    render(frame["type"], frame["payload"])
```

- The async generator `bus.stream()` IS the in-process outlet AND the SSE source — one
  abstraction, both consumers (#3). The server adds only SSE framing; no per-event logic.
- `stream_delta` (per-token, #15) rides the same channel as a high-frequency event;
  opt-in (server subscribes it, in-process SDK may not) — it does not persist (#15).
- Replay consumers use the stream as a **render projection**, not the storage SSoT. Events
  may include bounded render-grade payloads needed for row #12/#29 (`user_message`,
  `stream_delta.text`, tool name, safe structured args, result preview/status) plus
  `session_id`/`trace_id` refs. Large bodies stay in session/trace stores; replay hydrates
  refs when it needs exact full content. This keeps stores authoritative while making the
  SSE/JSONL stream sufficient to reconstruct a turn.
- **"add event = 2 places" survives the SSE path**: the endpoint loops over generic
  frames, so a new event needs zero SSE-layer change. ✔ Confirmed against e2e #12
  (replay script reconstructs turn from generic frames → no per-event renderer needed).

---

## Pydantic event base class design (#9)

- `name: ClassVar[str]` — the wire `type`; the SSoT for the event identity. No enum.
- `blockable: ClassVar[bool]` — carries dispatch policy WITH the event (not at call site).
- `namespace: ClassVar[str]` — `""` for kernel (bare name), `"dream"` etc. for plugins;
  validated in `__init_subclass__` so a malformed event name fails at import (fail-loud,
  earliest possible).
- `model_config = {"frozen": True, "extra": "forbid"}` — events are immutable value objects
  (no subscriber mutates a shared payload — avoids the cybernetics #4 multi-writer trap),
  and `forbid` makes a typo'd payload field crash at construction (schema-first, CLAUDE.md).
- Payload = **references + metadata plus bounded render payloads** (`session_id`, `turn_id`,
  `tool_name`, safe args/result preview, `*_ref`). Never make the event stream the full
  message-body SSoT; session/trace stores own bodies. The bounded render fields exist only
  so CLI `stream-json` and SSE replay can reconstruct the user-visible turn without
  per-event custom fetch logic for small payloads.
- `serialize()` is the single generic function; `model_dump(mode="json")` makes it
  SSE-ready (datetimes/UUIDs/enums → JSON scalars) with no custom serializer.

This matches the 2026 convention (bubus `BaseEvent(BaseModel)`, agent-event-bus dotted
namespaces) while staying enum-free and validation-at-definition.

---

## Industry refs with URLs

- **bubus** (browser-use) — production pydantic event bus, sync+async handlers via one
  `on`, `'*'` wildcard, event = `BaseEvent(BaseModel)` subclass. Closest production analog;
  validates the pydantic-event + single-`on` + wildcard design.
  https://github.com/browser-use/bubus
- **agent-event-bus / aiopubsub** — dotted-namespace wildcard matching (`tool.*`,
  `*.failed`), `add_async_listener` / `add_sync_listener`. Validates the glob scheme.
  https://pypi.org/project/aiopubsub/ ·
  https://quantlane.com/blog/aiopubsub/
- **asyncio TaskGroup vs gather** (Python 3.14 docs) — TaskGroup cancels siblings on first
  failure; gather does not. Basis for choosing isolated-gather (not TaskGroup) for
  independent Ring-3 fan-out. https://docs.python.org/3/library/asyncio-task.html
- **gather `return_exceptions` pitfalls** — why bare `return_exceptions=True` + discarded
  list is the swallow trap; catch-inside-task is the fix.
  https://superfastpython.com/asyncio-gather-exception/ ·
  https://fixdevs.com/blog/python-asyncio-gather-error/
- **Build an event bus with asyncio** (Jan 2026) — emit/subscribe/error-handling reference
  for a single-loop bus. https://oneuptime.com/blog/post/2026-01-25-event-bus-asyncio-python/view
- **Pydantic serialization** — `model_dump(mode="json")` for wire-ready dicts; basis for
  the generic `{type, payload}` rule. https://docs.pydantic.dev/latest/concepts/serialization/
- **pydantic_ai.ui** — `transform_stream` dispatches pydantic events → SSE protocol events;
  precedent for one stream → SSE framing at the boundary.
  https://ai.pydantic.dev/api/ui/base/

---

## Open questions

1. **`stream()` back-pressure.** A slow SSE client's unbounded `asyncio.Queue` grows
   without limit. Options: bounded queue + drop-oldest for `stream_delta` (transient,
   #15) but never-drop for lifecycle events; or per-sink overflow → emit a
   `stream.lagged` event. Decide before server-shell build. Lean: bounded queue,
   drop only delta frames, lifecycle frames block-or-warn.

2. **`subscriber.error` namespace.** It is kernel-owned but reads naturally as dotted
   (`subscriber.error`). Either (a) allow a kernel-owned dotted name (small exception to
   "kernel = bare"), or (b) rename to bare `subscriber_error`. Recommend (a) + a documented
   carve-out: kernel may own dotted names; the rule is really "plugins MUST be dotted +
   own-prefix", not "kernel MUST be bare". Tighten the validator wording accordingly.

3. **Subscriber timeout value.** The policy is no longer open: background subscribers are
   supervised with `asyncio.timeout(N)` and timeout emits `subscriber.error`. The remaining
   tuning question is only the default `N` and whether long-running subsystems should
   override it per subscriber.

4. **Ordering across patterns.** `_match` concatenates exact → glob → `*` bucket order;
   within a bucket, registration order. Is a deterministic cross-pattern order needed
   (e.g. should `pre_tool` exact subscribers always run before `tool.*` ones for veto
   priority)? For blockable veto this matters. Propose: blockable dispatch sorts by
   specificity (exact > glob > `*`) so the most-specific guard vetoes first. Confirm with
   loop+budget module (shares pre_tool).
