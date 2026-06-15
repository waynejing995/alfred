# Runtime Reality Slice Goal

Date: 2026-06-15
Status: active
Related architecture: docs/specs/2026-06-15-runtime-composition-design.md
Related decisions: docs/decisions/2026-06-15-runtime-composition-decisions.md
Related plan: docs/plans/2026-06-15-003-feat-runtime-reality-slice-plan.md

## Objective

Extract a small backend-only runtime slice that can be tested through real Alfred entry paths,
instead of keeping advanced modules in a toy/prototype state.

The first slice does not need the TUI/frontend. It must prove that backend runtime events and
stores are real by driving them through public `agentkit` APIs, CLI commands, or server/eval
entry points.

## Non-Toy Definition

A capability in this slice is non-toy only when all of these are true:

| Requirement | Meaning |
|---|---|
| Public entry path | The behavior is reached through `Agent`, CLI, server, cron, or eval, not by manually instantiating the target object in a test. |
| Real backing state | The behavior writes to or reads from its intended store/registry/event source. |
| Observable output | The entry path returns an id, event frame, artifact, or persisted row that proves the behavior happened. |
| Failure is loud | Missing required config, missing backing state, or unreachable tools fail explicitly. |
| Testable now | The slice has a local test that can run without the future TUI. |

## Backend Milestone Boundary

The current repo only has backend `agentkit`. TUI/frontend consumption is intentionally out of
scope for this goal. For this milestone, events are real when the backend runtime emits them and
SDK/CLI/server/eval tests can observe them. TUI rendering is a later consumer proof.

## Target Slice

| Slice | Entry path | Observable proof |
|---|---|---|
| SDK trace slice | `Agent(..., trace_store=...).run(...)` | `TurnResult.trace_id` and trace store rows/JSONL steps |
| CLI trace slice | `alfred chat --trace-db ... --output-format json|stream-json` | output includes `trace_id`; trace DB has the tool step |
| MCP slice | `Agent(config=mcp stdio server).run(...)` | model-initiated MCP tool call through registry |
| Goal slice | public `Agent` path with bound goal tools | goal store mutation plus synthetic continuation under gates |
| Eval slice | `alfred eval run ...` | durable artifacts with non-null rollout trace IDs |

## Success Criteria

1. A public SDK test records a trace step through `Agent.run()` and returns a non-null
   `trace_id`.
2. A CLI test can opt into trace recording and verify JSON/stream-json includes `trace_id`.
3. At least one non-local tool source, starting with stdio MCP, is reachable from the public
   `Agent` path.
4. Goal continuation has a backend entry-path test that does not require TUI.
5. Eval rejects missing trace IDs and writes durable artifacts for rollouts.
6. Prototype-only claims remain explicit until their entry-path test exists.

## Out Of Scope

- TUI rendering.
- Full runtime composition owner in one large refactor.
- Real cross-vendor LLM matrix.
- Production sandboxing.
- Distill/dream/evolve full behavior before the trace/eval substrate is real.

## Stop Conditions

- If a unit can only be tested by manually constructing its internal class, it stays out of this
  reality slice.
- If a feature requires the future TUI to observe it, defer that observation to the TUI milestone
  and keep the backend proof focused on event frames or persisted state.
