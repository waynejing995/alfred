---
title: Alfred Runtime Reality Slice Plan
type: feat
status: active
date: 2026-06-15
origin: docs/goals/2026-06-15-runtime-reality-slice-goal.md
decisions: docs/decisions/2026-06-15-runtime-composition-decisions.md
---

# Alfred Runtime Reality Slice Plan

## Overview

Extract a backend-only slice that proves Alfred runtime behavior through public entry paths.
The goal is not to implement the full `AgentRuntime` architecture in one jump; it is to promote
the first few prototype surfaces into real, testable behavior.

## Problem Frame

The audit identified useful modules that remain toy because tests instantiate them directly.
This plan creates a narrow reality gate: SDK, CLI, MCP, goal, and eval paths must produce real
observable state without waiting for the TUI/frontend.

## Requirements Trace

- R1. Backend-only: no TUI/frontend requirement for this slice.
- R2. Public entry path proof: no direct-object-only tests for promoted behavior.
- R3. Trace must be observable through `trace_id` plus persisted trace rows.
- R4. CLI JSON and stream-json must carry `trace_id` when tracing is enabled.
- R5. MCP must be reachable as a tool source through public `Agent` construction.
- R6. Goal must self-continue through backend runtime inputs, not TUI callbacks.
- R7. Eval must write durable artifacts and reject missing rollout trace IDs.

## Scope Boundaries

- Do not build the full final `AgentRuntime` in one unit.
- Do not use TUI as a dependency or verifier.
- Do not claim distill/dream/evolve are real until trace and eval slices exist.
- Do not silently register tools whose backing state is missing.
- Change git state only when explicitly requested, after verification and review.

## Context

### Relevant Code and Patterns

- `agentkit/agent.py` is the public SDK facade and current composition point.
- `agentkit/kernel/loop.py` owns `TurnResult` and tool dispatch.
- `agentkit/stores/trace/recorder.py` already records events when attached to a bus.
- `agentkit/stores/trace/sqlite.py` is the real trace store.
- `agentkit_cli/output.py` is the JSON/stream-json terminal payload SSoT.
- `agentkit_cli/main.py` constructs the CLI `Agent`.
- `agentkit/mcp/manager.py` has a real stdio MCP library surface.
- `agentkit/subsystems/goal/*` has store and driver pieces but no public entry wiring.
- `agentkit_eval/runner.py` currently reads `trace_id` from the result but does not require it.

### Constraints from Existing Plans

- The original agent-loop plan says subsystems must be wired back into entry paths before they
  count as complete.
- The runtime composition design adds a backend milestone boundary: lack of TUI is expected and
  must not block backend event/store proof.
- Existing OpenTUI docs in this worktree are unrelated to this backend slice.

## Key Technical Decisions

- Promote behavior one slice at a time through real entry paths, not by broad refactor.
- Start with trace because eval, distill, evolve, and debug all depend on real trace IDs.
- Keep `trace_id` optional in general `TurnResult`, but require it in trace-enabled paths.
- Add CLI opt-in tracing before making trace default, so existing simple CLI behavior stays stable.
- Treat MCP and goal as separate follow-up slices after trace proves the runtime event/store path.

## Open Questions

### Resolved During Planning

- **Does the current lack of TUI make backend events toy?** No. Backend events are real when emitted
  by the runtime bus and observed by SDK/CLI/server/eval tests.
- **Should the first slice be broad runtime composition?** No. First slice is trace-through-public-
  Agent because it is small and unlocks later real tests.
- **Should `trace_id` always appear in JSON output?** No. Include it only when tracing is enabled.

### Deferred to Implementation

- **Trace config shape:** CLI can start with `--trace-db`; full `AgentConfig.trace` can follow.
- **Trace sealing policy:** first slice records steps and turns; later verifier/eval units decide
  success/failure sealing.
- **MCP aliasing:** first MCP slice can use existing namespaced names; built-in `fff` alias is a
  later `fff`-specific unit.

## E2E Verification Contract

This plan carries the backend reality-slice contract from
`docs/specs/2026-06-15-runtime-composition-design.md`. `wayne-verify` is the sole mutator of
the `Status` column. Use `/Volumes/data/User/wayne/Code/alfred/.env` for real LLM credentials,
mapping `LLM_API_KEY` to `OPENAI_API_KEY` and passing `LLM_BASE_URL` through unchanged as an
OpenAI-compatible `/v1` `--base-url`. The current gateway model list was checked through
`/v1/models`; use `openai/mimo-v2.5-pro` for this contract.

| # | User path | Env: process | Env: data | Env: entrypoint | Observable (pass = ?) | Status |
|---|---|---|---|---|---|---|
| 1 | Developer asks Alfred CLI to use `hashread` on a real file while trace recording is enabled. | No daemon; one CLI process. Real LiteLLM provider via main worktree `.env`. | Temp file containing `ALFRED_REAL_TRACE_CONTENT`; temp `trace.db`. | `uv run alfred chat "Use hashread..." --provider litellm --model openai/mimo-v2.5-pro --base-url "$LLM_BASE_URL" --env-key OPENAI_API_KEY --tool-choice hashread --trace-db "$TRACE_DB" --output-format json` | JSON final message includes `ALFRED_REAL_TRACE_CONTENT`; `tool_trace[0].name == "hashread"`; result payload has non-empty `trace_id`; trace DB row for that id has a `hashread` step with the temp file path. | ✅ evidence: `/var/folders/b6/q7tnsx1974gb0jhjkj023ltw0000gn/T/alfred-openai-runtime-e2e-vfwjmiig/row1-hashread-json`; trace_id `eb1d00a6-10cc-4c80-b6fd-85a373663b1d` |
| 2 | Developer asks Alfred CLI for a streamed real LLM answer while trace recording is enabled. | No daemon; one CLI process. Real LiteLLM provider via main worktree `.env`. | Temp `trace.db`. | `uv run alfred chat "Reply exactly..." --provider litellm --model openai/mimo-v2.5-pro --base-url "$LLM_BASE_URL" --env-key OPENAI_API_KEY --trace-db "$TRACE_DB" --output-format stream-json` | JSONL contains at least one `stream_delta`; final `result` frame includes the requested token and non-empty `trace_id`; trace DB row exists for that id. | ✅ evidence: `/var/folders/b6/q7tnsx1974gb0jhjkj023ltw0000gn/T/alfred-openai-runtime-e2e-vfwjmiig/row2-stream-json`; trace_id `b7b1f546-05ff-473e-883e-9fd9b0a801a0` |

## Implementation Units

- [x] **Unit 1: SDK Trace Reality Slice**

  **Goal:** Let public `Agent.run()` record a trace when a trace store is supplied.
  **Requirements:** R1, R2, R3
  **Dependencies:** Existing trace store and recorder.
  **Decision trace:** RC1, RC3, RC7, RC13

  **Files:**
  - Modify: `agentkit/agent.py`
  - Modify: `agentkit/kernel/loop.py`
  - Modify: `agentkit_cli/output.py`
  - Test: `tests/integration/test_runtime_reality_slice.py`

  **Approach:**
  - Add optional `trace_store` to the public `Agent` facade.
  - Attach `TraceRecorder` to the bus inside `Agent.run()`.
  - Return the generated trace id on `TurnResult`.
  - Keep JSON output unchanged unless `trace_id` is present.

  **Patterns to follow:**
  - Existing `tests/integration/test_trace_store_e2e.py` for trace assertions.
  - Existing `tests/test_agent_facade.py` for public Agent entry shape.

  **Test scenarios:**
  - Happy path: mock model calls a real tool through `Agent.run()` -> result has `trace_id` ->
    trace store contains the tool step and success annotation.
  - Edge case: `Agent.run()` without trace store -> result has no `trace_id`, existing output tests
    remain unchanged.
  - Error path: tool failure -> trace store records failure annotation.

  **Verification:**
  - `uv run pytest tests/integration/test_runtime_reality_slice.py tests/cli/test_output_formats.py`

- [x] **Unit 2: CLI Trace Opt-In**

  **Goal:** Let `alfred chat` opt into real trace recording and expose `trace_id` in JSON/JSONL.
  **Requirements:** R2, R3, R4
  **Dependencies:** Unit 1
  **Decision trace:** RC3, RC7, RC10, RC13

  **Files:**
  - Modify: `agentkit_cli/main.py`
  - Modify: `agentkit_cli/output.py`
  - Test: `tests/cli/test_output_formats.py`
  - Test: `tests/integration/test_runtime_reality_slice.py`

  **Approach:**
  - Add a minimal `--trace-db PATH` CLI option.
  - Construct `SQLiteTraceStore` when the option is present.
  - Include `trace_id` in JSON and final stream-json result frames.
  - Keep text output unchanged.

  **Patterns to follow:**
  - Existing `--session-db` CLI wiring.
  - Existing SQLite store test setup.

  **Test scenarios:**
  - Happy path: `alfred chat ... --trace-db trace.db --output-format json` returns `trace_id` and
    the DB can load that trace.
  - Edge case: text output still prints only final message.
  - Error path: invalid trace path fails loudly through click.

  **Verification:**
  - CLI tests prove tracing through the real command, not through direct recorder construction.

- [ ] **Unit 3: MCP Public Agent Slice**

  **Goal:** Make stdio MCP usable from public `Agent` construction and prove the model can call an
  MCP tool through the normal registry/dispatch path.
  **Requirements:** R2, R5
  **Dependencies:** Unit 1
  **Decision trace:** RC4, RC6

  **Files:**
  - Modify: `agentkit/control/config.py`
  - Modify: `agentkit/agent.py`
  - Modify: `agentkit/mcp/manager.py`
  - Test: `tests/integration/test_runtime_reality_slice.py`

  **Approach:**
  - Add minimal MCP config support for stdio server declarations.
  - Connect MCP sources before prompt assembly/tool list freeze.
  - Register discovered MCP tools in the same `ToolsRegistry`.
  - Close MCP sessions after the runtime path completes.

  **Patterns to follow:**
  - Existing `tests/mcp/test_mcp_register.py`.
  - Existing `tests/integration/test_mcp_manager_e2e.py`.

  **Test scenarios:**
  - Happy path: public `Agent(config={mcp: ...})` calls `math.add` from a temp stdio MCP server.
  - Error path: declared required MCP server missing -> startup failure, not silent tool absence.

  **Verification:**
  - Test starts a real stdio MCP server and reaches it through `Agent.run()`.

- [ ] **Unit 4: Goal Backend Slice**

  **Goal:** Wire goal tools and continuation through backend runtime state without a TUI.
  **Requirements:** R2, R6
  **Dependencies:** Unit 1
  **Decision trace:** RC4, RC8, RC13

  **Files:**
  - Modify: `agentkit/agent.py`
  - Modify: `agentkit/tools/goal.py`
  - Test: `tests/integration/test_runtime_reality_slice.py`

  **Approach:**
  - Bind goal tools only when a goal store is supplied.
  - Let `turn_end` continuation enter through the same backend input path.
  - Keep no-progress and budget gates backend-observable through goal state.

  **Patterns to follow:**
  - Existing `tests/integration/test_goal_driver_e2e.py`.
  - Existing `agentkit/subsystems/goal/driver.py`.

  **Test scenarios:**
  - Happy path: public path sets a goal and produces one allowed synthetic continuation.
  - Edge case: paused goal does not continue.
  - Error path: repeated no-progress state changes goal status to `no_progress`.

  **Verification:**
  - Goal store changes are visible without constructing `GoalDriver` directly in the test.

- [ ] **Unit 5: Eval Trace Artifact Slice**

  **Goal:** Make eval consume real runtime trace IDs and write durable artifacts.
  **Requirements:** R2, R3, R7
  **Dependencies:** Units 1 and 2
  **Decision trace:** RC10, RC13

  **Files:**
  - Modify: `agentkit_eval/runner.py`
  - Modify: `agentkit_eval/types.py`
  - Modify: `agentkit_cli/main.py`
  - Test: `tests/eval/test_run_experiment.py`
  - Test: `tests/integration/test_eval_harness_e2e.py`

  **Approach:**
  - Require trace-enabled runtime for eval rollouts.
  - Fail if any rollout has missing `trace_id`.
  - Write `findings.json`, `rollouts.jsonl`, and `report.md` to a results directory.

  **Patterns to follow:**
  - Existing parity guard tests.
  - Existing eval type models.

  **Test scenarios:**
  - Happy path: mock eval writes artifacts with non-null trace ids.
  - Error path: runtime without trace support fails eval harness explicitly.

  **Verification:**
  - Eval tests inspect artifact files, not only returned dictionaries.

## Dead Code / Legacy Cleanup

- [Legacy] `tests/integration/test_trace_store_e2e.py` direct recorder test — keep as store/recorder
  coverage, but it no longer counts as public runtime proof.
- [Prototype] Distill/dream/evolve direct engine tests — keep as library tests until their runtime
  entry-path units are planned separately.
- [Shared] `agentkit/stores/trace/recorder.py` — keep; Unit 1 promotes it from direct-test-only to
  public path usage.

## System-Wide Impact

- **Interaction graph:** `Agent.run()` can attach trace recording; later units add CLI/MCP/goal/eval
  entry points over the same backend evidence.
- **Error propagation:** Missing opt-in trace config is not an error; missing required trace in eval
  is an error.
- **State lifecycle risks:** Reusing one trace recorder across turns would mix tasks; each external
  task must start its own trace id.
- **Unchanged invariants:** TUI remains out of scope. The kernel loop still does not construct stores.

## Risks & Dependencies

| Risk | Mitigation |
|------|------------|
| The first slice becomes another partial composition point | Keep it narrow, document it as the first step toward `AgentRuntime`, and require public-entry tests. |
| Trace IDs appear in old output unexpectedly | Include `trace_id` only when non-null. |
| Eval accepts mock-only proof as real proof | Separate mock mechanics tests from live profile requirements in the goal doc. |
| MCP connection failures silently remove tools | Required MCP sources fail at startup. |
| Goal continuation turns into hidden polling | Drive continuation from `turn_end` and backend input queue only. |

## Sources & References

- **Goal:** docs/goals/2026-06-15-runtime-reality-slice-goal.md
- **Runtime composition spec:** docs/specs/2026-06-15-runtime-composition-design.md
- **Runtime decisions:** docs/decisions/2026-06-15-runtime-composition-decisions.md
- **Trace recorder:** agentkit/stores/trace/recorder.py
- **Trace store:** agentkit/stores/trace/sqlite.py
- **Public facade:** agentkit/agent.py
- **CLI output:** agentkit_cli/output.py
