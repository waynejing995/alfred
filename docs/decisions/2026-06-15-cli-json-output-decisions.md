# Decision Log: CLI JSON / Streaming-JSON Output

Started: 2026-06-15
Status: design-approved (compact round — reuses event-bus SSoT; no full grill)
Parent spec: ../specs/2026-06-15-alfred-design.md
Parent decisions: ./2026-06-15-hermes-agent-loop-decisions.md (32) + layered-instructions (L1-L9) + tools-permission (T1-T11)
Plan: ../plans/2026-06-15-001-feat-alfred-agent-loop-plan.md

## Context

User: "do we have json output and streaming json output? without this it's hard to test and
debug in CLI mode." Real gap — the event-bus already emits structured `{type, payload}` (#9)
and `bus.stream()` exists (#3/#15), but there is no CLI **outlet** that prints it as JSON /
streaming-JSON to stdout (the Codex / Claude Code `--output-format json|stream-json` feature).
Compact round: high reuse of existing event-bus/SSE, so decisions proposed and ratified
without a full branch-by-branch grill.

## Decisions

| # | Question | Decision | Rationale | Source |
|---|----------|----------|-----------|--------|
| J1 | Which output modes? | **Three: `text` (default, human) / `json` (whole turn → one object: final message + tool trace + usage) / `stream-json` (one JSON object per event, JSONL, realtime).** `alfred chat --output-format <mode>`. | Mirrors Codex/Claude Code. `json` for eval/scripts grabbing the result; `stream-json` for realtime debug/TUI. Both requested. | user |
| J2 | Format SSoT? | **Reuse event-bus `serialize()` verbatim** — `stream-json` line = `{type, payload}` (the same #9 generic serialization the SSE layer uses); `json` = the turn's events aggregated into one terminal object. NO new format. | One serialization source → CLI-JSON and SSE-JSON can never drift (#9 SSoT). | user |
| J3 | `stream_delta` per mode? | **`stream-json`: `stream_delta` ON by default** (per-token, #15); **`json`: OFF** (terminal state only). | Debug wants token-level; result-grab wants no noise. | user |
| J4 | Relation to e2e #12 SSE replay-script? | CLI `stream-json` and the SSE replay-script are **the same consumer pattern over two transports** (stdout JSONL vs over-wire SSE); replay/parse logic is shared, not duplicated. | Delete>Add — one consumer model, two transports. | user |
| J5 | Which unit? | **Unit 7** (the CLI's birthplace) — JSON output is the CLI's output layer; `stream_delta` is already a Unit 3 / loop product. Not split into its own unit (basic CLI capability). | CLI outlet belongs to the CLI unit; not worth a separate unit. | user |
| J6 | e2e? | **New row #29:** `alfred chat --output-format stream-json` emits valid JSONL AND a replay reconstructs the turn (symmetric with #12 SSE). | Observable + testable — exactly the "easy to test/debug" the user asked for. | user |
