# Decision Log: Runtime Composition De-Toy Architecture

Started: 2026-06-15
Status: design-approved
Parent spec: ../specs/2026-06-15-alfred-design.md
Audit input: /Volumes/data/User/wayne/Code/alfred/docs/research/2026-06-15-toy-implementation-audit.md
Spec: ../specs/2026-06-15-runtime-composition-design.md

## Context

The toy implementation audit found the same failure pattern across trace, goal, memory tools,
subagent tools, MCP, SSE, eval, cron, distill, dream, and evolve: modules exist and are often
unit-tested directly, but the shipped entry paths do not compose them into a live runtime.

Current milestone note: this repo currently has only the backend `agentkit` runtime surface.
The absence of a TUI/frontend consumer is expected until the TUI work lands. An event or hook is
not toy merely because no frontend renders it yet. For the backend milestone, the threshold is
that the runtime emits the event on the canonical bus and at least one backend-facing outlet
or test can observe it. Human-visible TUI verification belongs to the later frontend milestone.

The kernel turn loop itself is not the toy. The missing piece is a session-level runtime owner
that constructs the bus, stores, tool sources, subscribers, lifecycle hooks, and close semantics
once, then gives CLI, SDK, server, cron, and eval the same runtime surface.

## Decisions

| # | Question | Decision | Rationale | Source |
|---|---|---|---|---|
| RC1 | What owns session/runtime composition? | Add `AgentRuntime` as the only session-level composition owner. `Agent` becomes a thin facade over it. | `Agent.run()` currently creates a fresh local `EventBus` per call and only attaches a capture sink. That makes trace, goal, MCP, Ring-3 subscribers, and scheduler unreachable from the main path. | audit + code |
| RC2 | What remains in the kernel loop? | Keep `run_turn()` focused on one turn: provider call, tool dispatch, permission, budget, and lifecycle event emits. It must not construct stores or subsystems. | The loop is the stable plant. Runtime composition belongs one layer above it, otherwise Ring-3 leaks into Ring-1. | decisions #4b/#7 |
| RC3 | What is the non-toy threshold? | A feature counts as shipped only when it is reachable from at least one public entry path and has an entry-path test. During the migration slice, the public `Agent` facade and CLI are valid entry paths; once `AgentRuntime` lands, new runtime capabilities must pass through it. Direct object tests alone prove only a library surface. | This converts the audit definition of toy into a design rule without blocking the backend-only reality slice on the full runtime owner refactor. | audit |
| RC4 | How are tools registered? | Register all tools through one `ToolsRegistry` owned by `AgentRuntime`. Register bound subsystem tools only when their backing state exists. | Naked functions such as `set_goal(store, ...)` and `spawn_subagent(spawner, ...)` must not appear as active affordances unless the runtime can bind their dependencies. | audit |
| RC5 | How is `fff` corrected? | Supersede the companion-package design for active runtime. `fff` becomes an in-box built-in MCP tool source, not a direct local tool depending on `agentkit_fff_<platform>`. | Owner correction says built-in MCP is the desired mechanism. The current companion package has no binary and should not satisfy the native `fff` contract. | audit owner note |
| RC6 | How does MCP integrate? | `AgentConfig` gains MCP source declarations and the runtime creates one `MCPManager` per runtime session. Built-in MCP sources and user-configured MCP servers feed the same registry adapter. | MCP is a tool source, not a second dispatch path. A declared MCP server that cannot connect fails at startup unless explicitly marked optional. | decisions #21 + audit |
| RC7 | How are traces wired? | The runtime starts a trace for each external task, attaches `TraceRecorder` to the runtime bus, and returns `trace_id` in the turn/run result. Tool events must carry result status, latency, and result reference before traces are used for learning. | Trace store and recorder are real but currently attached only by tests. Eval and learning subsystems need real trace IDs and usable step data. | decisions #17/#18a + audit |
| RC8 | How does goal self-continuation run? | Goal is a runtime subscriber plus bound tools. It injects synthetic inputs through a runtime-owned input queue after `turn_end`, never by mutating `run_turn()`. | Push, do not poll. The loop stays unaware; the runtime owns multi-turn continuation. | decision #19 |
| RC9 | How do server and SSE become real? | The server keeps a runtime registry keyed by session/thread and `/events` subscribes to the runtime event stream. Buffered replay may exist only as a persistence supplement, not as the source of truth. | Current SSE replays frames after `/turn`; it is not a live bus stream. | audit |
| RC10 | How do eval results become trustworthy? | Eval must run through `AgentRuntime`, require non-null trace IDs, write durable artifacts, and distinguish mock harness tests from live-provider eval proof. | Current eval sets `trace_id` from a missing `TurnResult` field and writes one dict. | audit |
| RC11 | What happens to prototype subsystems? | Distill, dream, and evolve stay prototype-only until they are runtime subscribers with real inputs and proposal lifecycle. They must not be presented as active shipped capabilities before that. | Their current engines are useful skeletons but not the documented behavior. | audit |
| RC12 | What gets deleted or demoted? | Delete or explicitly mark prototype-only any registry/config/branch that has no next-milestone runtime owner: direct companion `fff`, unused `judge_failure`, refundable budget without a refundable tool, and config/doc claims for unregistered tools. | Delete beats add. Leaving feature-shaped no-ops is the drift source. | audit + invariants |
| RC13 | How do we judge events before TUI exists? | During the agentkit-only milestone, an event is real when it is emitted by the runtime bus with schema and backend-observable tests. TUI rendering is a later consumer proof, not a prerequisite for backend event reality. | The current repo intentionally has no frontend yet. The event producer contract must land before the TUI can render it. | user |

## Cybernetics Model

| Element | Alfred runtime meaning |
|---|---|
| Plant | The active agent session: model calls, tool calls, stores, events, and user-visible outputs |
| Controller | `AgentRuntime`, plus config, permission resolver, autonomy gate, and event subscribers |
| Setpoint | Every advertised capability is reachable, observable, permissioned, and testable from a public entry path |
| Disturbance | Optional stores, missing MCP servers, mock-only tests, provider failure, config drift, feature-shaped prototype code |
| Feedback | Event stream, trace store, session store, CLI JSONL, live SSE, eval artifacts, entry-path tests |

## Supersedes

- Supersedes the active-runtime part of tool decision T5: companion `agentkit_fff_<platform>`
  packages are no longer the design for `fff` as a shipped Alfred tool. A native executable may
  still exist internally, but it is hidden behind the built-in MCP source.
- Tightens the original agent-loop plan's "attach subsystems in dependency order" language:
  attachment means construction by `AgentRuntime` plus entry-path proof, not just object existence.
