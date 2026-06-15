---
title: Alfred — Minimal Agent-Loop SDK Implementation Plan
type: feat
status: active
date: 2026-06-15
origin: docs/specs/2026-06-15-alfred-design.md
decisions: docs/decisions/2026-06-15-hermes-agent-loop-decisions.md
---

# Alfred — Minimal Agent-Loop SDK Implementation Plan

## Overview

Alfred is a minimal, frontier-design agent-loop kernel shipped SDK-first (pure-Python
core, zero UI deps) with every advanced capability (memory, skills, distill, dream,
evolve, goal, fusion, handoff, mcp) as a swappable, A/B-able subsystem. This plan
sequences the build kernel-first: Tier-0 (Ring-1 kernel + permission + one tool + one real
provider + CLI) reaches a working end-to-end conversation first. Ring-2 stores
(session/trace/memory/skill-loader) then attach in dependency order and must be wired back
into the frozen-prefix/session lifecycle before they are considered complete. Each unit maps
~1:1 to a commit.

## Problem Frame

Thesis (KB `synthesis-harness-over-model`): the harness, not the model, is the dominant
variable. So the kernel must stay tiny and every experiment must be add/removable to
measure whether it actually helps. The build is a learning vehicle for agent design;
correctness of the kernel is the geology all Ring-2/3 experiments stand on, so the
kernel ships first and is proven alive (one real conversation) before subsystems load.
See origin spec §1, §8.

## Requirements Trace

- R1. Pure-Python core (`agentkit`) with zero UI and zero HTTP knowledge; consumers
  (`agentkit-cli`, `agentkit-server`, `agentkit-eval`) sit outside it (spec §2).
- R2. Ring-1 kernel: loop, context-assembly+cache discipline, iteration budget,
  event-bus, 5 registries, `ModelProvider` ABC (spec §3).
- R3. Ring-2 stores behind interfaces with default impls: session (SQLite WAL+FTS5),
  memory (files+RRF retrieval, swappable), trace (3-level schema), skill-loader
  (multi-root, L0/1/2, `.versions/` invisible) (spec §4).
- R4. Ring-3 subsystems as event-driven plugins, each disable-able/A/B-able, writing only
  to existing stores, calling models via injected providers never the loop: distill,
  dream, evolve, goal, fusion, handoff, mcp (spec §5).
- R5. Three-layer autonomy control (off/assist/auto, default assist) + per-loop confirm
  gates; gate lands before the first auto-loop (spec §6.1, L8).
- R6. Single pydantic `AgentConfig` SSoT, YAML-primary with `{type,params}` recursion,
  `env_key`/`${ENV}` secret indirection, `extra="forbid"` (spec §6.2).
- R7. Real-LLM e2e across BOTH vendors (Anthropic + OpenAI/Azure); fusion forced
  cross-vendor; cache-hit verified by usage numbers (spec §10, Decision #26/#29).
- R8. SSE data outlet + replay-script e2e (#12) so a future TUI is zero-rework (spec §12,
  Decision #25).
- R9. Self-measuring eval harness as a consumer package, sharing `score_rollouts()` with
  evolve (spec §13, Decision #32, CEO 10th star).
- R10. 1 commit = 1 unit; each unit independently committable, signed off as the human
  (Decision #27).
- R11. Tool-call permission model (allow/ask/deny + pattern, per-agent narrow, composed with
  autonomy); built-in tool catalog baseline (hashline read/edit, fff search, bash, web_fetch
  with SSRF guard) — tools-permission decision log T1-T11.

## Scope Boundaries

- **TUI render layer = OUT** (spec §12.1). The SSE outlet + e2e #12 replay criterion ARE
  in — but no terminal UI is built. Future work via its own mind-explode.
- **git-worktree multi-agent file isolation = OUT** (spec §12.2). MVP isolation covers
  context/tool/state layers only.
- **Formal plugin packaging/distribution = OUT** (spec §12.3). Registry mechanism exists;
  packaging is later.
- **Production path** (gateways/multi-platform/sandboxing) = OUT (spec §12.4).
- **Darwin-Gödel archive for evolve = OUT**; MVP builds the oracle-mutator-selector
  kernel only, leaving the upgrade seam (Decision #18a).
- **swarm/peer multi-agent = OUT**; only orchestrator-worker + sequential handoff built
  (Decision #23).
- **Anthropic native `compact_20260112` = OUT of default**; A/B fallback only.

## Context

### Relevant Code and Patterns

- Repo `/work/alfred` is empty except `LICENSE` + initial commit — greenfield, no
  existing code to mirror inside the repo.
- External patterns to follow (named in research notes): Hermes Agent `agent/skill_utils.py`
  (skill loader), Hermes SQLite WAL params (session store), Codex `~/.codex/config.toml`
  `[model_providers.x]` structured blocks (provider config), Anthropic Agent Skills /
  agentskills.io SKILL.md frontmatter standard (skill format), agentevals list-of-dicts
  JSONL (trace bodies).
- Per-module research in `docs/research/<module>.md` (17 files) carries verified interface
  sketches, field names, and gap resolutions — each unit cites its file.

### Constraints from Existing Plans

- None. No other plans exist in `docs/plans/`. Spec §11 confirms no code conflicts and no
  dead code (greenfield).

## Key Technical Decisions

- **SDK-first, three rings, three+1 packages** — core has zero UI/HTTP deps; eval is a
  consumer not a Ring-3 subsystem (Decisions #1, #4b, #22, #32).
- **Self-describing pydantic events, no central enum** — adding a kernel event touches 2
  places; dispatch policy lives on `Event.blockable` (Decisions #5/#7/#9).
- **Await-free `reserve()`** — single-threaded asyncio makes a no-await critical section
  atomic; no Lock/Semaphore for budget (H1, Decision research kernel-loop-budget).
- **`ModelProvider` ABC owns Alfred's pydantic types; LiteLLM is one swappable impl** —
  only `litellm_provider.py` imports litellm (Decisions #10/#28).
- **fusion = composite provider, not a registry/loop change** — sub-providers injected at
  construction; aggregator is a constructor param (Decisions #10/#11).
- **handoff = extension of subagent** — payload schema + `handoff` event is the sole
  coupling surface; isolation enforced at construction (Decisions #23/#23a).
- **session = one frozen-prefix epoch, not one process** — epoch roll at turn_end when
  skill/memory dirty; cron = fresh session per tick (H3).
- **Autonomy gate is constructor-required on every auto-loop** — a loop cannot be
  constructed ungated; lands before goal (L8, Decision #20c).
- **`extra="forbid"` on owned config, `extra="ignore"` on untrusted LLM/skill input** —
  per KB `llm-prompt-and-boundary-contracts` (Decisions #13, M7).

## Open Questions

### Resolved During Planning

- **Package naming** (Alfred vs agentkit): user-facing name = Alfred; Python packages =
  `agentkit` / `agentkit-server` / `agentkit-cli` / `agentkit-eval` (Decision #24, spec §2).
- **Where the cache stuck-at-zero WARNING lives**: in the loop, not the provider (provider
  only populates `Usage.cached_tokens`) — research kernel-context-cache cross-file note.
- **Aggregator as 6th registry?** No — constructor param of FusionProvider (Decision #11).
- **eval as Ring-3?** No — consumer package; a subsystem cannot spawn loops (Decision #32).

### Deferred to Implementation

- **LiteLLM `wire_api="responses"` for the Azure key-proxy** — **CONFIRMED real (2026-06-15):**
  `~/.codex/config.toml` `[model_providers.custom]` declares `wire_api="responses"`, so the gateway
  DOES require the OpenAI Responses API (not Chat Completions). No longer an open question — Unit 4
  must route the Azure worker to the Responses API; still validated end-to-end at e2e #1.
- **`api_base` vs `base_url` / `extra_query` exact kwargs** — pinned at e2e #1 against the
  live gateway (Decision #32).
- **Memory backend choice** — RESOLVED by user after audit: no Zep/Graphiti default in MVP
  because the useful Zep/Graphiti path brings graph DB / service and embedding dependencies.
  MVP returns to the low-dependency files+SQLite baseline so dream can operate on simple
  human-readable facts. Zep/Graphiti remains a future `MemoryProvider` adapter/A-B arm, not
  a runtime dependency.
- **Exact fleet width W / merge batch B_merge for distill** — bounded by a Semaphore,
  tuned at implementation against rate limits (research subsystem-distill).

## Plan Alignment Corrections (Subagent Audit)

Two independent read-only audits were run before continuing beyond Unit 10. These corrections
are now part of the plan and are blocking for future "done" claims:

- **Store scope SSoT:** DB is only for session, trace, and memory facts/indexes. Skill content
  and versions are pure files under active skill dirs and `.versions/`; goal is JSON; distill
  cursors/proposals live in `trace.db` meta tables. Do not reintroduce a DB skill-version
  ledger or startup db-vs-file drift check.
- **Tier-0 meaning:** Unit 7 is the v1 vertical slice. Session, trace, memory, and skill-loader
  are Ring-2 follow-on units; the overview and verification language must not claim they were
  part of Tier-0 unless they are actually wired.
- **Context assembly completeness:** `Agent` session_start must assemble the full frozen order:
  `persona -> user -> project_instructions -> memory(facts) -> skill_l0`. A store unit is only
  "complete" when its data can be wired into this prefix or when the plan explicitly marks it
  as a store-only milestone with a later integration gate.
- **Memory backend decision:** MVP memory is low-dependency files + SQLite FTS/entity/recency
  retrieval, no embedding dependency. Zep/Graphiti is future adapter work. Do not call the
  default implementation "semantic cosine" unless embeddings are actually implemented.
- **`allowed-tools` enforcement:** plan must define a concrete `ToolCallContext` / permission
  scope before coding skill enforcement. Skill `allowed-tools` narrows tool permissions only
  while executing an explicit skill invocation, not merely because the model viewed a skill.
- **`ask` permission:** Unit 6 needs a minimal `ConfirmHandler`/headless policy if it interprets
  `ask`; Unit 13 later upgrades the global autonomy gate. Do not leave ask-confirm behavior
  implicit.
- **Event veto:** `pre_tool` veto must propagate as a control signal, not be swallowed as a
  generic tool exception.
- **Real e2e contract vs unit e2e:** unit/e2e tests may use mocks for local regression, but
  `wayne-verify` real contract rows #1/#17/#29 must run the actual CLI/provider path. Both
  vendors are mandatory in the verify profile.
- **`fff` packaging:** Unit 7b is incomplete until a real companion package/binary path exists
  or the plan explicitly downgrades `fff` to fallback-only. The decision remains "bundled
  per-platform binary"; fallback tests alone do not satisfy it.
- **`stream-json`:** row #29 requires `stream_delta` frames from a streaming provider path.
  Lifecycle-only JSONL is not sufficient.
- **`score_rollouts()` dependency:** evolve cannot depend on Unit 24. Either run eval-harness
  before evolve or extract `score_rollouts()` into a small shared unit before Unit 19.

## Implementation Units

Units are grouped into 5 milestones. **Milestone A = Tier-0**: the first wave; its
completion (Unit 7) is the v1 done-criterion — one real end-to-end conversation. Later
milestones attach subsystems in dependency order. 1 unit = 1 commit unless an
atomically-coupled group is noted.

### Milestone A — Tier-0 Kernel (v1 = one end-to-end conversation)

- [ ] **Unit 1: Provider types + ModelProvider ABC + MockProvider**

  **Goal:** Alfred's own message/response pydantic types and the `ModelProvider` ABC the
  whole loop depends on, plus a deterministic `MockProvider` for unit tests (no network).
  **Requirements:** R2
  **Dependencies:** None (foundation)
  **Decision trace:** #10, #28; research provider-layer.md

  **Files:**
  - Create: `agentkit/kernel/providers/types.py` (zero litellm import)
  - Create: `agentkit/kernel/providers/base.py` (`ModelProvider` ABC)
  - Create: `agentkit/kernel/providers/errors.py`
  - Create: `agentkit/kernel/providers/mock.py` (`MockProvider`)
  - Create: `pyproject.toml` (package `agentkit`, py3.12+, uv), `agentkit/__init__.py`
  - Test: `tests/kernel/providers/test_types.py`, `test_mock_provider.py`

  **Approach:**
  - Owned types: `ContentBlock` (`type`, `text`, `cache_control: dict|None`), `ToolCall`
    (`id`, `name`, `arguments: dict` parsed, `raw_arguments: str`), `Message` (`role`,
    `content: str|list[ContentBlock]|None`, `tool_calls`, `tool_call_id`, `name`),
    `ToolDef` (`name`, `description`, `parameters: dict`), `Usage` (`prompt_tokens`,
    `completion_tokens`, `total_tokens`, `cached_tokens`, `cache_creation_tokens`),
    `ModelResponse` (`message`, `usage`, `finish_reason`, `model`, `raw`), `StreamDelta`,
    `ToolCallFragment`.
  - ABC: `async complete(messages, tools=None, tool_choice=None, **params) -> ModelResponse`
    + `stream(...) -> AsyncIterator[StreamDelta]` (two methods, no `stream: bool` flag).
  - Error classes: `ProviderAuthError`, `ProviderRateLimit`, `ProviderTimeout`,
    `ProviderUnavailable`, `ProviderContextExceeded`, `ProviderBadRequest`, `ProviderError`.
  - `extra="ignore"` on types that receive model output; required fields stay required
    (KB `llm-prompt-and-boundary-contracts`).

  **Patterns to follow:**
  - research provider-layer.md interface sketch (verbatim field names).

  **Test scenarios:**
  - Happy path: `MockProvider.complete([user msg])` → `ModelResponse` with populated `usage`.
  - Edge case: a `Message` with `content=None` + `tool_calls` serializes/round-trips.
  - Error path: malformed tool-call JSON in a response → `ProviderBadRequest` at the boundary.

  **Verification:**
  - `MockProvider` drives a turn with no network; all owned types round-trip via pydantic.

  **Integration/regression test:** `tests/integration/test_mock_provider_e2e.py` — construct a real `Agent` with
  `MockProvider` (no network) and run one real turn through the actual public API; assert the
  returned `ModelResponse` has populated `message` + `usage` fields. Proves the type/ABC contract
  works end-to-end through the real facade, not just in isolation.

  **E2E contract rows:** none — pure interface/types; observable only via #1, #2 once the
  loop exists.

- [ ] **Unit 2: Event bus (self-describing pydantic events + dispatch policy)**

  **Goal:** the pub/sub bus with sync-veto (blockable) + async-isolated (background)
  dispatch, wildcard subscription, generic serialization, and the `subscriber.error`
  visibility channel (H2).
  **Requirements:** R2
  **Dependencies:** None (foundation, parallel to Unit 1)
  **Decision trace:** #5, #7, #9; research kernel-event-bus.md

  **Files:**
  - Create: `agentkit/kernel/events/base.py` (`Event` base, `serialize()`)
  - Create: `agentkit/kernel/events/defs.py` (kernel event classes)
  - Create: `agentkit/kernel/events/bus.py` (`EventBus`)
  - Test: `tests/kernel/events/test_bus.py`, `test_event_defs.py`, `test_add_event_2_places.py`

  **Approach:**
  - `Event(BaseModel)` ClassVars `name`, `blockable=False`, `namespace`; `frozen=True`,
    `extra="forbid"`; `__init_subclass__` validates names (kernel bare `^[a-z][a-z0-9_]*$`,
    plugin `prefix.suffix`); `RESERVED = {"", "kernel", "alfred", "sys"}`.
  - `serialize(ev) -> {"type": ev.name, "payload": ev.model_dump(mode="json")}` — the ONLY
    serialization site.
  - `EventBus`: `on(pattern, fn, owner_ns="")`, `off`, `register_emitter`, `emit`,
    `stream()`. Dispatch keyed off `ev.blockable`: `_emit_blocking` (sequential, first
    raise = veto) vs `_emit_isolated` (per-sub try/except + `asyncio.gather`, NOT TaskGroup).
  - Background failures: `logger.error(...)` + emit `SubscriberError` (`subscriber.error`);
    re-entrancy guard (if failing event IS `subscriber.error`, log only, one hop max).
  - Kernel events: `session_start`, `turn_start`, `pre_tool` (blockable), `post_tool`,
    `turn_end`, `session_end`, `idle`, `tick`, `job_due`, `skill_used`, `budget_warning`,
    `budget_exhausted`, `handoff`. Payload = references+metadata only.

  **Patterns to follow:**
  - research kernel-event-bus.md; "add event = 2 places" invariant (KB
    `incomplete-feature-coverage`).

  **Test scenarios:**
  - Happy path: `on("turn_end", fn)` fires; `on("*")` receives all; `on("tool.*")` matches prefix.
  - Edge case: a blockable subscriber raises → veto propagates to emitter.
  - Error path: a background subscriber raises → `subscriber.error` emitted + logged, loop unaffected.
  - Invariant: adding a new kernel event touches only `defs.py` + one emit site (regression test).

  **Verification:**
  - Both dispatch paths behave per policy; `subscriber.error` visible; serialization generic.

  **Integration/regression test:** `tests/integration/test_event_bus_e2e.py` — instantiate a real `EventBus`, subscribe
  one sync-veto handler that returns a veto and one async-isolated handler that raises, then emit a
  real event. Assert the veto stops downstream dispatch, the raising background subscriber produces a
  `subscriber.error` event, and the bus stays live for a subsequent emit.

  **E2E contract rows:** #18 (subscriber-raises survival).

- [ ] **Unit 3: Context assembly + layered instruction discovery + frozen-prefix cache discipline**

  **Goal:** per-turn system-prompt assembly — **discover and merge layered instructions**
  (persona/user `core/` + tree-walked, merged `AGENTS.md`), freeze into a static prefix
  with a cache breakpoint; the `session = frozen-prefix epoch` model with epoch-roll at
  turn_end (H3). This unit defines *what the system prompt is made of, where it is
  discovered, and how it merges* — previously under-specified.
  **Requirements:** R2, R7
  **Dependencies:** Unit 1 (types), Unit 2 (epoch-roll events)
  **Decision trace:** #21, #29; research kernel-context-cache.md; **layered-instructions
  decision log L2-L9** (`docs/decisions/2026-06-15-layered-instructions-decisions.md`)

  **Files:**
  - Create: `agentkit/kernel/context.py` (`FrozenPrefix`, `ContextAssembler`, `CacheUsage`)
  - Create: `agentkit/kernel/instructions.py` (`InstructionResolver`, `ResolvedInstructions`
    — discovery + merge + per-segment budget; separate concern, separately unit-tested,
    reusable by server)
  - Test: `tests/kernel/test_context.py`, `test_cache_fields.py`, `test_instructions.py`

  **Approach:**
  - `FrozenPrefix(frozen=True)`: `tools`, `persona`, `user`, `project_instructions`,
    `skill_l0`, `memory`, `goal`, `fingerprint` (sha256 drift detector). Freeze order =
    **`persona → user → project_instructions → memory(facts) → skill_l0`** (most-stable
    first → max cache hit; L6).
  - `InstructionResolver.resolve(cwd, alfred_home) -> ResolvedInstructions`:
    - **Global layer:** `{ALFRED_HOME}/AGENTS.md` (default `~/.alfred`, overridable by
      `ALFRED_HOME` env var only; L3) — added explicitly, NOT via the walk.
    - **Project layer:** walk up from `cwd` to git root, stop at git root; if `$HOME` is
      reached with no git root → **fall back to cwd single-layer** (bounded, never traverses
      the filesystem; L4a). Walk never goes above `$HOME`.
    - **Per-directory filename:** `AGENTS.md` > `CLAUDE.md`, take only one per layer (L5);
      **cross-layer merges**, order = **global first, nearest last** (L4).
    - **Do NOT** recognize `~/.claude/CLAUDE.md` (L5a).
    - **Merge-then-freeze:** discover + merge ONCE at session_start, freeze into prefix;
      edits take effect next session / on epoch-roll (L4b).
  - **Budget + failure (L7):** the char cap (default ~20k, configurable) is a **soft
    reminder threshold, NOT a hard limit** — over-cap = **WARNING only, full content kept
    uncut** (no truncation; truncating would silently drop user-authored instructions).
    Never error, never block `skill_l0`/`memory` loading (each frozen-prefix segment has its
    own independent budget). No AGENTS.md anywhere = DEBUG (normal). A *declared* source that
    can't be read (permission / missing-after-declared / decode failure) = **fail-loud**.
  - **Observability (L9-F1):** at session_start emit a **DEBUG-only resolved-instruction
    manifest** (ordered layers: path + char-count + included/skipped/over_cap), shown under
    `-v`. The two WARNINGs (read-failure, over-cap-but-included-uncut) stay default-visible.
    Normal load = quiet; fault = loud.
  - **Disable/A-B (L8):** `instructions.enabled` config toggle (default true) for eval-harness
    A/B arms.
  - `ContextAssembler(prefix, rolling_breakpoints=0)`: `assemble(tail) -> AssembledPrompt`
    (frozen prefix + tail, breakpoint at end of prefix); `compress(tail)` summarize-middle,
    prefix untouched, `protect_last_n=20`.
  - Epoch roll: subsystems emit `skill.changed`/`memory.changed` → set `prefix_dirty`; at
    next `turn_end` if dirty, re-assemble → new `FrozenPrefix`/`fingerprint`, tail
    preserved, re-emit `session_start`; governed by autonomy (lands fully once Unit 15 exists —
    flag-only here, wired in Unit 15).
  - `CacheUsage`: `cached_tokens` (`usage.prompt_tokens_details.cached_tokens`),
    `cache_read` (`usage.cache_read_input_tokens`), `cache_write`
    (`usage.cache_creation_input_tokens`), `prompt_tokens` (includes cached — never infer
    cache state from it). Invariants kept in a small high-attention slot with a byte-cap
    test (KB `llm-prompt-and-boundary-contracts`).

  **Patterns to follow:**
  - research kernel-context-cache.md verified field names + cache floors.
  - opencode rules (walk-up + merge) + Hermes prompt-assembly (tier separation) — see
    layered-instructions decision log Research Inputs.

  **Test scenarios:**
  - Happy path: global + project `AGENTS.md` both present → merged with nearest layer last;
    fingerprint stable across calls; breakpoint at prefix end.
  - Edge case: same dir has both `AGENTS.md` + `CLAUDE.md` → only `AGENTS.md` taken; non-git
    cwd → single-layer + global only.
  - Edge case: merged instructions exceed cap → WARNING only, full content kept uncut;
    **`skill_l0` still loads normally** (per-segment budget isolation).
  - Edge case: `prefix_dirty` set → epoch roll at turn_end produces new fingerprint, same tail.
  - Error path: a declared instruction source can't be read → fail-loud. No AGENTS.md at all
    → DEBUG, agent runs normally.
  - Error path: mid-turn mutation attempt rejected (frozen prefix).

  **Verification:**
  - Prefix frozen within an epoch; cache field names match verified spec; layered merge order
    is global→nearest; `-v` shows the resolved-instruction manifest.

  **Integration/regression test:** `tests/integration/test_instruction_layering_e2e.py` — build a temp dir tree with a
  global `~/.alfred/AGENTS.md` and a project `./AGENTS.md` inside a real git repo, then run the real
  `InstructionResolver`. Assert the merged frozen prefix contains both layers in nearest-wins order,
  the fingerprint is stable across two resolves, and an over-cap layer logs a WARNING yet keeps its
  content.

  **E2E contract rows:** #23 (layered merge + nearest-wins), #24 (instruction-source
  read-failure fail-loud). Also enables #17 (#17 needs the real provider in Unit 4).

- [ ] **Unit 4: LiteLLMProvider + provider config (the only litellm import)**

  **Goal:** the default `ModelProvider` impl wrapping LiteLLM behind the ABC, with
  per-vendor cache_control handling and the verified usage-field reads; provider config
  with `env_key` indirection.
  **Requirements:** R2, R6, R7
  **Dependencies:** Unit 1 (ABC + types)
  **Decision trace:** #26, #28, #29, #32; research provider-layer.md

  **Files:**
  - Create: `agentkit/kernel/providers/litellm_provider.py` (ONLY file importing litellm)
  - Create: `agentkit/kernel/providers/config.py` (`LiteLLMParams`, `ProviderConfig`, factory)
  - Test: `tests/kernel/providers/test_litellm_provider.py` (mock litellm), `test_provider_config.py`

  **Approach:**
  - `LiteLLMProvider(model, api_key, base_url=None, http_headers=None, query_params=None,
    extra=None)`; methods `_to_litellm_messages`, `_block` (model-aware: pass
    `cache_control` for Anthropic, strip for OpenAI/Azure), `_to_litellm_tools`,
    `_call_kwargs`, `_to_response`, `_to_usage`, `_map_exc`.
  - Verified kwargs: `api_base` (litellm kwarg) ← `base_url`; `extra_headers` ←
    `http_headers`; `extra_query` ← `query_params` (Azure `api-version`); `stream=True` +
    `stream_options={"include_usage": True}` + `litellm.stream_chunk_builder(...)`.
  - **Real env (verified 2026-06-15, see E2E contract header):** Anthropic base URL + API key via
    `~/.claude/settings.json` `env` (`ANTHROPIC_BASE_URL=http://127.0.0.1:8888`,
    `ANTHROPIC_API_KEY`); model id from `ALFRED_REAL_MODEL` or the current process'
    `ANTHROPIC_DEFAULT_*_MODEL` env (do not invent a model setting in the Claude settings file).
    OpenAI/Azure via `~/.codex/config.toml` `[model_providers.custom]` — **`wire_api="responses"`
    (Responses API, NOT Chat Completions; Decision #32 risk now CONFIRMED real)**,
    `api-version=2025-04-01-preview`, header `Ocp-Apim-Subscription-Key`, base `…/openai`. Provider
    must support routing to the Responses API for the Azure worker. Secrets via `env_key` only —
    the real codex config's plaintext header key must be re-expressed as `${ENV}` (never copied).
  - `LiteLLMParams(extra="forbid")`: `model`, `env_key`, `base_url`, `http_headers`,
    `query_params`, `api_version`, `extra`. Factory `build_litellm_provider(p)` resolves
    `os.environ[env_key]`, fail-loud if unset.
  - `_map_exc` dict maps litellm exceptions → Alfred error classes.
  - **Empirically probe** LiteLLM streaming/tool-call delivery for target models before
    finalizing (KB `external-cli-sdk-integration`).

  **Patterns to follow:**
  - research provider-layer.md; Codex `[model_providers.x]` structured block + `env_key`.

  **Test scenarios:**
  - Happy path: `complete()` round-trips Alfred types ↔ litellm types (litellm mocked).
  - Edge case: OpenAI model → `cache_control` stripped from blocks; Anthropic → passed.
  - Error path: missing `env_key` env var → fail-loud at factory; litellm RateLimitError → `ProviderRateLimit`.

  **Verification:**
  - Only `litellm_provider.py` imports litellm (grep regression test); usage fields populate.

  **Integration/regression test:** `tests/integration/test_litellm_provider_e2e.py` — drive a real `LiteLLMProvider`
  against the local proxy loaded from the real local config files; no cassette for the live e2e.
  Assert the response round-trips into Alfred types and usage fields are readable. Add a grep
  assertion that only `litellm_provider.py` imports `litellm` anywhere in the package.

  **E2E contract rows:** none yet at unit-level (real-vendor #1/#17 land at Unit 7 once the
  loop + CLI exist; this unit is their precondition).

- [ ] **Unit 5: Iteration budget (await-free reserve, single owner)**

  **Goal:** the iteration-budget primitive with synchronous await-free `reserve()`/`refund()`,
  shared total cap with per-agent ledgers, and budget warning/exhausted events (H1).
  **Requirements:** R2
  **Dependencies:** Unit 2 (budget events)
  **Decision trace:** #7; research kernel-loop-budget.md

  **Files:**
  - Create: `agentkit/kernel/budget.py` (`IterationBudget`, `Grant`)
  - Test: `tests/kernel/test_budget.py`

  **Approach:**
  - `Grant(frozen)`: `agent_id`, `n`, `refundable`, `_id`. `IterationBudget(total_cap,
    warn_at_frac=0.8)`: `reserve(agent_id, n=1, *, refundable=False) -> Grant|None`
    (sync, await-free), `refund(grant)` (idempotent via `_refunded` set), `remaining`,
    `spent_by`, `reconciles()`.
  - Single-owner injected into every sub-loop by constructor; same instance, distinct
    `agent_id` keys. Invariant: `total_remaining + sum(ledger) == total_cap`.
  - `budget_warning` fires once via `_warned` (re-arms on refund crossing back);
    `budget_exhausted` level-triggered/idempotent. Refundable = a tool-registry property.

  **Patterns to follow:**
  - research kernel-loop-budget.md; no Lock/Semaphore (single-threaded asyncio atomicity).

  **Test scenarios:**
  - Happy path: reserve down to 0, `reconciles()` holds throughout.
  - Edge case: refund an already-refunded grant → no double-credit (idempotent).
  - Error path: reserve when exhausted → returns None + `budget_exhausted` fired.

  **Verification:**
  - Two-counter reconcile invariant holds under interleaved reserve/refund across agent_ids.

  **Integration/regression test:** `tests/integration/test_iteration_budget_e2e.py` — run a real `IterationBudget` inside
  an asyncio loop with interleaved reserve/refund calls across several distinct `agent_id`s. Assert
  the `reconciles()` invariant holds at every step and that exhausting the budget fires a real
  `budget_exhausted` event.

  **E2E contract rows:** none at unit level (negative-path #19 lands at Unit 7 with the loop).

- [ ] **Unit 6: Five registries + tool dispatch + permission gate + AgentConfig skeleton**

  **Goal:** the 5 registries (tools/events/models/skill_sources/middleware), the minimal
  `AgentConfig` to construct a Tier-0 agent, and **the permission gate woven INTO
  `_dispatch`** — permission is an L0 dispatch-level invariant pulled forward into Tier-0,
  not a late subsystem (tools-permission decision T2).
  **Requirements:** R2, R6, **R11 (permission)**
  **Dependencies:** Unit 1, Unit 2
  **Decision trace:** #8, #13, #30a; tools-permission T1, T2, T3, T9, T10, T11-F; research control-autonomy-config.md

  **Files:**
  - Create: `agentkit/kernel/registries.py` (5 registry types + `RegistryEntry`)
  - Create: `agentkit/kernel/permission.py` (`Permission` enum allow/ask/deny, `PermissionResolver`,
    pattern-match, strictest-merge)
  - Create: `agentkit/control/config.py` (`AgentConfig`, `ComponentSpec`, resolver — skeleton)
  - Test: `tests/kernel/test_registries.py`, `test_permission.py`, `tests/control/test_config_skeleton.py`

  **Approach:**
  - Registries: `tools` (name+schema+handler), `events`, `models`, `skill_sources`,
    `middleware`. Mechanism open, catalog converged (no speculative tables).
  - Introduce `ToolCallContext` now: `agent_id`, optional `skill_name`, `permission_layers`,
    `autonomy_level`, `interactive`, and `confirm_handler`. Unit 6 owns raw permission resolution
    and the minimal ask/headless policy; Unit 13 replaces/extends `autonomy_level` with the full
    `AutonomyGate` object. This prevents `ask` semantics from floating until Unit 13.
  - **Permission gate (T1/T3):** `Permission` = allow/ask/deny. `_dispatch` consults
    `PermissionResolver` BEFORE running a tool. **Compose with autonomy (T3):** `allow`→run;
    `deny`→block (all autonomy levels, incl. auto — deny is a hard wall); `ask`→ off:deny /
    assist: interactive confirm (headless→deny, fail-loud log) / auto: allow. **Strictest-merge
    (T9):** config base → skill `allowed-tools` narrows → per-agent tool-scope narrows; lattice
    `deny > ask > allow`, pattern last-match-wins; layers can only narrow, never widen.
  - `AgentConfig(extra="forbid", frozen=True)` minimal Tier-0 fields: `model: ComponentSpec`,
    `skill_sources`, `budget`, **`permission` block** (per-tool / pattern rules). `ComponentSpec
    (extra="forbid")`: `type`, `params`. `resolve(spec, registry)` recurses nested specs. Full
    env layering / autonomy gate object / proposal store deferred to Unit 14.
  - Tool dispatch contract: `_dispatch(ctx, call)` → permission check → catches tool exceptions
    → `ToolResult(ok=False, is_error=True)`, re-raises/returns explicit `VetoError` path for
    `pre_tool` vetoes (not swallowed by the generic catch).
  - **Observability (T11-F):** a deny/ask that blocks a call logs a default-visible line naming
    tool + deciding layer; resolved per-tool permission joins the `-v` manifest (shared with
    instruction manifest, Unit 3).

  **Patterns to follow:**
  - opencode permission model (allow/ask/deny + pattern last-match-wins + per-agent narrow).
  - research control-autonomy-config.md ComponentSpec 2-phase validation.

  **Test scenarios:**
  - Happy path: register a tool; `read`-class tool resolves `allow` and runs; resolve a
    `{type,params}` model spec to a provider.
  - Edge case: `bash` with `rm *` pattern → deny even under autonomy=auto (hard wall);
    `ask` under headless (no TTY) → downgraded to deny + fail-loud log.
  - Edge case: skill `allowed-tools` narrows a config-`allow` to deny → strictest wins once Unit
    11 supplies a concrete `skill_name`/tool-scope; Unit 6 may test the resolver primitive, but
    not claim full skill enforcement before the skill loader exists.
  - Error path: unknown `type` → `UnknownComponentType`; extra key in config → crash.

  **Verification:**
  - All 5 registries register/lookup; `_dispatch` enforces allow/ask/deny composed with
    autonomy; strictest-merge across the 3 sites holds; minimal `AgentConfig` builds a Tier-0 agent.

  **Integration/regression test:** `tests/integration/test_permission_gate_e2e.py` — wire real tool/permission registries
  to the real `PermissionResolver` and dispatch a tool through the real `_dispatch` under composed
  allow/ask/deny rules. Assert `deny` hard-blocks even when autonomy is `auto`, and that an `ask`
  rule resolves to `deny` when running headless (no interactive responder).

  **E2E contract rows:** #25 (deny hard-wall blocks a dangerous tool even in auto), #26
  (ask→confirm interactive, headless→deny). (Mechanism otherwise internal.)

- [ ] **Unit 7: Agent loop + `hashread` tool + CLI (v1 end-to-end conversation)**

  **Goal:** wire loop + context + budget + registries + permission + provider into `run_turn`,
  ship the **`hashread`** tool (the Tier-0 demo tool — hashline read so #1 reads a file) and a
  thin CLI; prove the kernel alive with a real conversation on BOTH vendors. The rest of the
  tool baseline lands in Unit 7b. **This unit's completion = v1 done-criterion.**
  **Requirements:** R1, R2, R7
  **Dependencies:** Units 1-6
  **Decision trace:** #22; tools-permission T4 (hashline), T6; research kernel-loop-budget.md, kernel-context-cache.md

  **Files:**
  - Create: `agentkit/kernel/loop.py` (`run_turn`, `_dispatch`, `TurnCtx`, `TurnResult`)
  - Create: `agentkit/tools/file_hash.py` (`hashread` — emits `LINE:HASH|content`)
  - Create: `agentkit/agent.py` (public `Agent` facade for `import alfred`)
  - Create: `agentkit-cli/` package (`alfred chat`, `--continue` flag stub, `--output-format
    text|json|stream-json`) — thin pure-dispatch wrapper, zero agent logic (T8: launcher thin,
    kernel substantive)
  - Create: `agentkit-cli/output.py` (text / json / stream-json renderers over `bus.stream()`)
  - Test: `tests/kernel/test_loop.py` (MockProvider), `tests/test_agent_facade.py`,
    `tests/tools/test_hashread.py`, `tests/cli/test_output_formats.py`

  **Approach:**
  - `run_turn(ctx)`: assemble messages → `ctx.budget.reserve()` → `provider.complete()` →
    parse → permission-check + `_dispatch` tool → emit lifecycle events → repeat until no tool call.
    A `pre_tool` veto is a control signal and must propagate/return a vetoed tool result; it must
    not be swallowed by a broad tool-exception catch as an ordinary tool failure.
  - `hashread`: read file, tag each line `LINE:HASH|content` (2-char content hash) — the read
    half of the hashline pair; `read` permission = allow (T6).
  - Tool-call parsing: single-pass, exact-match, adversarial test inputs for malformed
    calls (KB `parser-asymmetry-pitfalls`).
  - Cache stuck-at-zero WARNING lives HERE (loop), not provider: turn≥2 + prefix≥floor +
    `cached_tokens==0` → WARNING.
  - CLI is a thin consumer of the public SDK; `import alfred; Agent(config).run(...)`. CLI must
    load/register the built-in tools by default (or via explicit config) so contract row #1 can
    exercise the real `hashread` path without hand-injected test fixtures.
  - `Agent` construction MUST wire `InstructionResolver` → `FrozenPrefix` → `ContextAssembler`
    into `TurnCtx`; e2e must inspect the provider messages and prove global/project instructions
    are present in the `system` message with the cache breakpoint attached.
  - **Output formats (cli-json-output J1-J5):** `--output-format text|json|stream-json`.
    `stream-json` = one `{type,payload}` JSONL line per event (= event-bus `serialize()`
    verbatim, J2), `stream_delta` ON; `json` = events aggregated into one terminal object
    (final message + tool trace + usage), `stream_delta` OFF; `text` = human default. NO new
    format — same serialization SSoT as SSE, so CLI-JSON and SSE never drift. `stream-json`
    e2e must assert at least one `stream_delta` frame when a streaming provider path is used; a
    MockProvider/lifecycle-only test is not sufficient for row #29.

  **Patterns to follow:**
  - research kernel-loop-budget.md `run_turn`/`_dispatch` sketch; oh-my-pi hashline read format.
  - event-bus `serialize()` (#9) — reused by both CLI output and SSE; Codex/Claude Code
    `--output-format stream-json` shape.

  **Test scenarios:**
  - Happy path: one turn with a `hashread` call returns final answer (MockProvider); output
    carries `LINE:HASH` tags.
  - Edge case: tool raises → tool-result message fed back, loop continues.
  - Edge case: `--output-format stream-json` → each line is a valid `{type,payload}` JSON event;
    `--output-format json` → single terminal object with message+tool-trace+usage.
  - Error path: budget exhausted mid-turn → clean stop message, ledger reconciles.

  **Verification:**
  - `alfred chat` answers a real question using `hashread`; `import alfred` returns a result object;
    all three `--output-format` modes produce well-formed output from the same event stream.

  **Integration/regression test:** `tests/integration/test_tier0_gate.py` (or split equivalents) — cover four real paths:
  (1) SDK/loop/tool path with a scripted `MockProvider` forcing a `hashread` call against a temp file
  and asserting `LINE:HASH|content` in the tool result; (2) provider-message inspection proving
  layered instructions are actually assembled into the system prefix and cache breakpoint; (3) real
  `alfred chat --output-format stream-json` subprocess output is valid JSONL and includes
  `stream_delta`; (4) live real-model smoke + two-turn cache-hit check against the local proxy.
  Contract row #1 additionally needs a deterministic real-provider tool-call path: either force
  `tool_choice=hashread` or use a fixture prompt proven against both Anthropic and OpenAI/Azure.
  Do not write "CLI + MockProvider triggers hashread" unless the CLI exposes a scripted
  provider/config hook that makes that true.

  **E2E contract rows:** #1 (both vendors), #2 (import), #17 (cache hit both vendors), #19
  (budget exhaustion clean stop), #29 (stream-json JSONL + replay).

- [ ] **Unit 7b: Tool baseline (hashedit / write_file / fff / list_dir / bash / web_fetch)**

  **Goal:** ship the rest of the built-in tool catalog baseline beyond `hashread`, each wired
  to its default permission bucket; bundle the `fff` binary via a per-platform companion package.
  **Requirements:** R1, R2, R11
  **Dependencies:** Unit 6 (permission), Unit 7 (`hashread` + loop)
  **Decision trace:** tools-permission T4, T5, T6, T6a, T7

  **Files:**
  - Create: `agentkit/tools/file_hash.py` (extend with `hashedit` — anchors on `LINE:HASH`, validates
    hash unchanged since read, returns fresh-hash diff), `agentkit/tools/write_file.py`,
    `agentkit/tools/search.py` (`fff` wrapper + ripgrep/python fallback chain), `agentkit/tools/list_dir.py`,
    `agentkit/tools/bash.py`, `agentkit/tools/web_fetch.py`
  - Create: `agentkit-fff-<platform>/` companion package skeleton (T5) + binary-locator in `search.py`
  - Test: `tests/tools/test_hashedit.py`, `test_write_file.py`, `test_search_fallback.py`,
    `test_bash_deny_patterns.py`, `test_web_fetch_ssrf.py`

  **Approach:**
  - **Packaging contract (T5):** `fff` is a real bundled executable delivered by
    per-platform companion packages (`agentkit-fff-linux-x64`, `agentkit-fff-macos-arm64`,
    `agentkit-fff-win-x64`). The main package discovers the matching companion package at runtime
    and falls back only when no supported binary is present. The companion package must include
    an executable `bin/fff` or equivalent locator; a package stub without a binary is not complete.
    The fff index/frecency state lives under `~/.alfred/fff/` (or `ALFRED_HOME/fff/`), never in the
    project tree.
  - **hashedit (T4/T6a):** anchor on `LINE:HASH`; if hash changed since read → reject (stale,
    fail-loud) — closes the "what I read == what I edit" invariant by mechanism, not by trusting
    model recall (KB `llm-prompt-and-boundary-contracts`). `hashread`+`hashedit` = paired system.
  - **write_file:** create/overwrite full content. Both `hashedit` + `write_file` ride ONE unified
    `write` permission bucket (default ask) — tools separate, permission unified (T6, opencode-proven).
  - **fff (T4/T5):** subprocess the bundled binary (located via `agentkit-fff-<platform>`); frecency
    search; `read` permission = allow. Fallback chain: bundled fff absent/unsupported → ripgrep →
    pure-Python grep, fail-loud WARNING. fff is NOT a Python dep (subprocess only).
  - **bash:** `ask` default; deny patterns `rm *`/`sudo *`/etc (pattern last-match-wins).
  - **web_fetch (T7):** httpx, built-in; default `ask`; **hard deny patterns: `localhost`,
    `127.0.0.1`, `169.254.169.254` (metadata), `10./192.168./172.16.` private ranges** — protects
    the local key-proxy `127.0.0.1:8888` from SSRF; fetched content marked untrusted on injection.

  **Patterns to follow:**
  - oh-my-pi hashline edit (default mutation surface, benchmark-validated); opencode unified write
    permission; ripgrep-bin per-platform companion packaging.

  **Test scenarios:**
  - Happy path: `hashedit` on a freshly-read line applies; `fff` returns frecency-ranked hits;
    `write_file` creates a new file.
  - Edge case: `hashedit` on a line whose hash changed since read → rejected (stale); fff binary
    missing → ripgrep fallback + WARNING.
  - Error path: `bash rm -rf` → deny; `web_fetch http://127.0.0.1:8888` → deny (SSRF guard).

  **Verification:**
  - All 7 baseline tools registered with correct default permission; real bundled fff binary path
    works on the current platform or the unit is explicitly marked fallback-only; fff fallback chain
    works; web_fetch SSRF denylist blocks internal addresses; hashedit rejects stale edits.

  **Integration/regression test:** `tests/integration/test_tool_baseline_e2e.py` — exercise the real tools end-to-end:
  `hashedit` a temp file then mutate it externally and assert the stale edit is rejected; run the
  bundled `fff` binary over a temp tree and assert backend=`fff` plus ranking/frecency behavior
  (separate fallback test covers rg/python); assert a `bash` deny pattern blocks; and assert a
  `web_fetch` to `127.0.0.1` is blocked by the SSRF guard. Also add a DNS-to-private regression
  before declaring SSRF complete.

  **E2E contract rows:** #27 (hashedit stale-edit rejected on real file), #28 (web_fetch SSRF
  denylist blocks `127.0.0.1:8888`). Also feeds #1 (hashread already covers the #1 file-read path).

### Milestone B — Ring-2 Stores

> **Store scope (RESOLVED — store-scope decision log S1-S6, S9):** all stores live under
> `~/.alfred/` (global home, NOT in-project — S1). Isolation = **a `project_id` column +
> `WHERE project_id=?`**, NOT per-project dirs/db files (S1a) — cross-project search = drop the
> WHERE. **Global (no project_id):** memory `core/` (persona/user). **Per-project (project_id):**
> session, trace, memory `facts/` (S2). `project_id` = normalized path of the project root,
> discovered via layered-instructions L4a (git-root walk, cwd fallback) — ONE "project"
> definition shared with instructions (S5); MVP accepts move/rename breakage (S3, relink = TODO).
> **DB scope is intentionally narrow** (S6/S9): DB is used for `sessions.db`, `trace.db`, and
> memory facts/indexes only. Skill content and versions are pure files (`SKILL.md`,
> `.versions/vN/`, `manifest.json`); goal state is a JSON file; distill cursors/proposal queue
> are small trace.db meta tables. **On-disk active `SKILL.md` is the SSoT for "what loads"**;
> archived versions are cold backup only and never scanned by the loader.

- [ ] **Unit 8: Session store (SQLite WAL + FTS5)**

  **Goal:** the conversation-record SSoT powering `--continue`/`--resume` and `session_search`.
  **Requirements:** R3
  **Dependencies:** Unit 1 (Message type)
  **Decision trace:** #17 (session vs trace boundary); store-scope S1-S5 (project_id column); research store-session.md

  **Files:**
  - Create: `agentkit/stores/session/base.py` (`SessionStore` ABC), `sqlite.py` (default impl),
    `types.py` (`SessionMeta`, `SearchHit`)
  - Create: `agentkit/stores/_sqlite.py` (shared WAL write helper — reused by trace/memory)
  - Create: `agentkit/stores/project.py` (`resolve_project_id(cwd)` — reuses layered-instructions
    L4a git-root walk; cwd fallback; normalized path)
  - Test: `tests/stores/test_session.py`, `test_session_fts.py`, `test_project_scope.py`

  **Approach:**
  - `SessionStore` ABC: `create_session(*, source, model, model_config, system_prompt,
    parent_session_id) -> str`, `add_message(session_id, msg) -> int` (complete messages only,
    never `stream_delta`), `get_messages(session_id, *, include_chain=True)`, `latest_session`,
    `list_sessions`, `search(query, *, limit, context_radius)`, `end_session`.
  - SQLite single `~/.alfred/sessions.db`: tables `sessions` (with **`project_id` column**,
    S1a/S2) / `messages` (`seq` per-session monotonic, reasoning in own columns) / `messages_fts`
    external-content FTS5 (`content=messages`, `tokenize='porter unicode61'`, 3 sync triggers) /
    `schema_version`. `MATCH ... ORDER BY rank`; `snippet()` with `>>>match<<<`. **Default queries
    filter `WHERE project_id=?`** (current project via `resolve_project_id`); cross-project search
    = drop the WHERE (S1a). `project_id` = normalized project-root path (git-root walk / cwd
    fallback, S3/S5).
  - Shared WAL helper (Hermes-verified): `timeout=1.0`, `synchronous=NORMAL`,
    `foreign_keys=ON`, `isolation_level=None`, `BEGIN IMMEDIATE`, 15 retries,
    `random.uniform(0.020,0.150)` jitter, `wal_checkpoint(TRUNCATE)` every 50 writes;
    WAL→DELETE fallback on NFS/SMB/FUSE with one-time WARNING.
  - `source` ∈ `cli|server|cron|subagent`; `end_reason` ∈ `normal|compression|branched|error`.
  - Schema-version-guarded reader sweep, re-validate via pydantic on read (KB `ssot-drift`).

  **Patterns to follow:**
  - research store-session.md; Hermes SQLite params.

  **Test scenarios:**
  - Happy path: two sessions, `latest_session` returns the newer; `search` finds turn-1 text.
  - Edge case: two projects' sessions in one db → default `WHERE project_id` isolates them;
    dropping WHERE searches across both. Parent chain rehydration in `seq` order.
  - Error path: concurrent writers → `BEGIN IMMEDIATE` + retry, no corruption.

  **Verification:**
  - Store-level: FTS5 `search` returns ranked hits with snippets and project scoping holds.
    Runtime-level row #3 is not complete until CLI/Agent `--continue` is wired to this store.

  **Integration/regression test:** `tests/integration/test_session_store_e2e.py` — open a real SQLite `sessions.db` in a
  temp dir and write two sessions under two different `project_id`s. Assert a `WHERE project_id`
  query isolates each project's turns and that an FTS5 search finds the turn text; then drop the
  `WHERE` clause and assert cross-project search returns both. Add a later CLI e2e for
  `alfred chat --continue` once Agent/CLI integration lands.

  **E2E contract rows:** #3 (turn-2 recalls turn-1 via FTS5).

- [ ] **Unit 9: Trace store (3-level schema, SQLite + JSONL, append-only)**

  **Goal:** the agent-learning raw-material SSoT — written by loop/subagents/handoff, read only
  by Ring-3 (distill/evolve/dream); separate from session.
  **Requirements:** R3
  **Dependencies:** Unit 8 (shared WAL helper, session pointer target), Unit 2 (event hooks)
  **Decision trace:** #17, #18a, #20b; research store-trace.md

  **Files:**
  - Create: `agentkit/stores/trace/base.py`, `sqlite.py`, `types.py` (`Annotation`, schemas),
    `detectors.py` (event-subscriber auto-detectors)
  - Test: `tests/stores/test_trace.py`, `test_trace_annotations.py`, `test_trace_seal_once.py`

  **Approach:**
  - 3-level schema: trajectory (`trace_id` ulid, `session_id` pointer, `parent_trace_id`,
    `agent_role`, `outcome`, `outcome_source`, `score`, `budget_used`, `handoff_payload`) /
    turn (`turn_id`, `assistant_msg_id` pointer, written at turn_end) / step (`step_id`,
    `kind`, `tool_name`, `tool_args` structured, `result_status`, `step_annotations`, `msg_id`
    pointer, written at pre/post_tool).
  - `Annotation`: `kind ∈ {success,failure,user_pushback,correction,off_track,user_approval}`,
    `source ∈ {user,auto,verifier,judge}`, append-only; trust ladder verifier>user>judge>auto.
    Detectors are push subscribers on post_tool/turn_end.
  - Storage: SQLite index `~/.alfred/trace.db` (`traces` w/ **`project_id` column** + `body_path`,
    `trace_skills`, `annotations` append-only) + JSONL bulk bodies `~/.alfred/traces/<trace_id>.jsonl`
    (one step/line). Reuse Unit 8 WAL helper + `resolve_project_id`. Default `WHERE project_id=?`;
    distill/evolve can widen for cross-project mining (store-scope S2/S1a).
  - **Terminal-write-once (KB `async-backend-contracts`):** seal trajectory behind a `_sealed`
    flag flipped only after the write returns; shield the finalizer; sync seal path. Append
    emits idempotent.
  - Query API: `replay_set(skill_name, min_outcome_quality)`, `failure_set`/`success_set`.
  - Candidate-filter (#20b): missing trace = silent DEBUG skip; detector *raising* = surface it.
  - Add a `TraceRecorder` event subscriber in this unit or a clearly named follow-up integration
    unit. Store-only direct tests do not prove the loop actually writes traces at `pre_tool`,
    `post_tool`, and `turn_end`.

  **Patterns to follow:**
  - research store-trace.md 3-level schema + annotation ladder.

  **Test scenarios:**
  - Happy path: loop writes trajectory+turn+step; `replay_set` returns scored rows.
  - Edge case: higher-trust annotation supersedes earlier `auto` (never collapse).
  - Error path: cancel mid-write → terminal seal lands exactly once (no double JSONL line).

  **Verification:**
  - trace separate from session; replay set reconstructable; seal-once under cancel. Runtime
    trace-capture verification is required before consumers (distill/evolve/dream) rely on it.

  **Integration/regression test:** `tests/integration/test_trace_store_e2e.py` — write a real trajectory to a real
  `trace.db` + JSONL in a temp dir while cancelling mid-write. Assert the terminal seal lands exactly
  once (no duplicate JSONL line for the trajectory) and that `replay_set` returns scored rows for
  the sealed trajectory. Add a loop-driven e2e that runs a real tool call and verifies trace rows
  were written by event subscribers before marking Unit 9 integrated.

  **E2E contract rows:** none directly (internal raw material; observed via #9 distill, #10
  evolve, #16 dream).

- [ ] **Unit 10: Memory store (low-dependency files + SQLite retrieval, swappable)**

  **Goal:** cross-session facts SSoT — `core/` always-injected + retrieved `facts/` via a
  low-dependency file/SQLite baseline; swappable `MemoryProvider` interface. Zep/Graphiti/mem0
  are future adapters/A-B arms, not MVP runtime dependencies.
  **Requirements:** R3
  **Dependencies:** Unit 8 (WAL helper), Unit 3 (frozen-prefix injection)
  **Decision trace:** #17a; research store-memory.md

  **Files:**
  - Create: `agentkit/stores/memory/base.py` (`MemoryProvider` ABC), `files.py` (default impl),
    `types.py` (`RetrievedMemory`, `MemoryBlock`, `MemoryWrite`), `index.py` (FTS5+entity+recency;
    no embedding dependency in MVP)
  - Create: `agentkit/tools/memory.py` (`memory_append`/`memory_replace`/`memory_search` tools)
  - Test: `tests/stores/test_memory.py`, `test_memory_rrf.py`

  **Approach:**
  - `MemoryProvider` ABC: `prefetch(ctx) -> RetrievedMemory` (ONCE at session_start, sync, on
    cache path), `sync_turn(writes, ctx)` (turn_end, async, no live-prefix mutation),
    `shutdown()`, optional `search(query, k=10)`.
  - Layout `~/.alfred/memory/`: `core/persona.md`+`core/user.md` (**GLOBAL, no project_id** —
    always verbatim; these ARE the persona/user instruction layers from layered-instructions L2),
    `facts/<slug>.md` (frontmatter `id`/`summary`/`entities`/`source_session`), `index.db`
    (rebuildable derived index; **facts rows carry `project_id`**, store-scope S2).
  - Retrieval = low-dependency passes (BM25 over summary/body, summary lexical overlap,
    entity overlap, and optional recency) fused by **RRF `score = Σ 1/(k + rank_i)`** (no tuned
    weights), top-k=10. **No embedding/semantic-cosine dependency in MVP.** Facts retrieval
    default `WHERE project_id=?` (current project only, S4); single index → "project+global
    merged retrieval" later = just widen the WHERE (seam left, not built). Zep/Graphiti's useful
    shape (temporal facts/entities/relations) remains a future `MemoryProvider` adapter; do not
    pull graph DB/embedding dependencies into the default store.
  - Frozen-prefix order is the full L6 order: persona → user → project_instructions →
    memory(facts) → skill_l0. Unit 10 must expose `prefetch()` data so `Agent` can feed
    `FrozenPrefix.memory`; otherwise it is only a store-only partial milestone. Mid-session
    `memory_*` writes durable store only, take effect next session. `memory_search` results
    turn-local. NO `memory_delete` for agent (dream-only).
  - Reuse Unit 8 WAL for `index.db`; per-fact atomic-rename writes.

  **Patterns to follow:**
  - research store-memory.md RRF + core/facts split.

  **Test scenarios:**
  - Happy path: `prefetch` returns top-k facts; `core/` always present.
  - Edge case: mid-session `memory_append` → durable but not in current frozen prefix.
  - Error path: corrupt fact file → skip + WARNING, retrieval continues.

  **Verification:**
  - core/ always injected; low-dependency RRF retrieval ranks relevant facts; current-project
    facts do not leak; swappable interface honored; `Agent` frozen prefix receives memory facts
    when a memory provider is configured.

  **Integration/regression test:** `tests/integration/test_memory_store_e2e.py` — populate a real memory store (files +
  index) in a temp dir with `core/` notes and project-scoped facts, then call the real `prefetch`
  and a real `Agent` session_start. Assert `core/` is always returned, RRF top-k facts are filtered
  to the requested `project_id` (facts from other projects excluded), and those facts appear in the
  frozen system prefix without mutating mid-session after `memory_append`.

  **E2E contract rows:** none directly at unit level (memory tidiness verified via #16 dream).

- [ ] **Unit 11: Skill loader + skill-store writer (multi-root, L0/1/2, `.versions/`)**

  **Goal:** the skill catalog SSoT (read side: multi-root scan, L0/1/2, precedence, `.versions/`
  invisible) + the atomic write side (M4 per-skill lock + os.replace) used by distill/evolve/revert.
  **Requirements:** R3
  **Dependencies:** Unit 3 (L0 into prefix), Unit 2 (`skill_used` event)
  **Decision trace:** #12, #14, #20a, #20a-i, M4, M7; research store-skill-loader.md

  **Files:**
  - Create: `agentkit/stores/skill/loader.py` (`build_catalog`, `load_skill`, `SkillFilter`,
    `Catalog`), `frontmatter.py` (`SkillFrontmatter` extra="ignore"), `writer.py`
    (`SkillStoreWriter` — M4 lock + atomic swap + manifest)
  - Create: `agentkit/tools/skill.py` (`skills_list`, `skill_view`)
  - Test: `tests/stores/test_skill_loader.py`, `test_skill_precedence.py`, `test_skill_writer_atomic.py`

  **Approach:**
  - Loader: roots in precedence order (`./skills` > `~/.alfred/skills` > `bundled`); identity =
    frontmatter `name`; same-name → first-wins + WARNING naming shadowed (e2e #5). L0 (name+desc)
    frozen into prefix; L1 full SKILL.md via `skill_view(name)` (fires `skill_used`); L2 via
    `skill_view(name, path)`. `iter_skill_dirs` skips any `.`-prefixed dir → `.versions/` invisible.
  - `SkillFrontmatter(extra="ignore")` lenient (untrusted third-party); `SkillFilter(extra="forbid")`
    owned. M7: catalog error (bad YAML/name≠dirname/unreadable) → WARNING+skip, recorded in
    `Catalog.errors`; config error (unknown `skill_filter` key) → crash.
  - **`allowed-tools` enforced** (tools-permission T10): feeds the permission narrow-layer for
    explicit skill execution only. `skill_view(name)` establishes a skill invocation context
    for the current skill-run/worker scope; the model merely viewing L1 does not permanently
    narrow all later unrelated tool calls. Multiple active skills must not merge into a broader
    permission set; the strictest currently executing skill scope wins.
  - **Storage = pure files (store-scope S6-narrowed; NO DB for skills):** active skill is a
    DIRECTORY (SKILL.md + `references/`+`scripts/`+`assets/`); version archives are whole-dir
    snapshots under `.versions/vN/` (incl. ref/script); `manifest.json` = active pointer + history
    (`version`, `origin`, parent, ts, lesson-bank ref, pass-rate). `origin ∈ {human, distill,
    evolve, revert}`. **On-disk SKILL.md is the single SSoT for "what loads"** — only ONE
    declaration of active, so no db-vs-file drift. Hand-edits natively supported (the file IS the
    SSoT; a manual edit just becomes the new active).
  - Writer (M4): single `SkillStoreWriter` owner shared by distill/evolve/revert; `asyncio.Lock`
    keyed by `skill_name`; archive whole dir → `.versions/vN/`; write `.SKILL.md.<uuid>.tmp` →
    `os.replace`; **`manifest.json` written last = commit point**. revert = copy `.versions/vN/`
    (whole dir) back to active.

  **Patterns to follow:**
  - research store-skill-loader.md; Hermes `skill_utils.py`; agentskills.io frontmatter.

  **Test scenarios:**
  - Happy path: skill in `./skills` loads to L0, `skill_view` returns L1, fires `skill_used`.
  - Edge case: same-name in two roots → higher wins + WARNING; `.versions/` not scanned.
  - Error path: corrupt skill → catalog-error skip + WARNING; good skills still load.

  **Verification:**
  - precedence + shadow WARNING; `.versions/` invisible; atomic writes; `allowed-tools` enforced;
    skill L0 is actually injected into the frozen prefix when the loader is configured.

  **Integration/regression test:** `tests/integration/test_skill_loader_writer_e2e.py` — set up real multi-root skill dirs
  in temp dirs with a same-name skill in two roots; assert the loader logs a shadow WARNING and
  skips `.versions/`. Then have the real writer add a skill via atomic `os.replace` + manifest;
  hand-edit a skill file on disk and assert the edited active `SKILL.md` is what loads (origin stays
  `human` or unchanged; do not invent `origin=manual`, which was removed by store-scope S8/S9).
  Also assert `allowed-tools` blocks a disallowed tool under an explicit skill invocation.

  **E2E contract rows:** #4 (skill loaded + adopted), #5 (same-name shadow WARNING), #22 (corrupt
  skill skip).

### Milestone C — Ring-3 Subsystems + Autonomy Gate

- [ ] **Unit 12: Subagent + handoff (isolation at construction)**

  **Goal:** the spawn primitive with construction-time isolation (context/tool/state) + handoff
  as its extension (payload schema + `handoff` event = sole coupling surface).
  **Requirements:** R4
  **Dependencies:** Unit 5 (budget), Unit 7 (loop), Unit 9 (trace), Unit 6 (registries/permission)
  **Decision trace:** #23, #23a; research subsystem-handoff.md

  **Files:**
  - Create: `agentkit/subsystems/handoff/spawner.py` (`Spawner`, `AgentSpec`, `_build_isolated_ctx`,
    `_scope_tools`), `payload.py` (`HandoffPayload`, `ArtifactRef`, `HandoffResult`), `events.py`
  - Create: `agentkit/tools/spawn.py` (`spawn_subagent`, `handoff_to`)
  - Test: `tests/subsystems/test_handoff_isolation.py`, `test_handoff_budget_ledger.py`

  **Approach:**
  - `Spawner.spawn(spec, payload, parent_trace_id) -> HandoffResult`; `_build_isolated_ctx`
    enforces 4 isolation layers at construction: L1 context (fresh `MessageHistory`, same
    `IterationBudget` instance keyed by distinct `agent_id` → separate ledger), L2 tool-scope
    (`_scope_tools` from explicit allowlist, default-deny, unregistered → `ToolScopeError`;
    feeds the permission narrow-layer T9), L3 state (own `trace_id`, `parent_trace_id`), L4 comms
    (payload + `handoff` event only).
  - `HandoffPayload(extra="forbid")`: `schema_version`, `from_agent`, `control ∈
    {returnable,one_way}`, `objective`, `output_format`, `context_refs`, `artifacts`, `extra`.
    `control` = 2-branch switch (returnable=await+append; one_way=Transfer sentinel).
  - Budget: spawn costs parent `reserve(1)`; two-counter invariant `total_remaining + Σledgers
    == total_cap`. Transfer record → trace store (feeds evolve).

  **Patterns to follow:**
  - research subsystem-handoff.md; orchestrator-worker + sequential only (no swarm).

  **Test scenarios:**
  - Happy path: orchestrator spawns worker, worker runs isolated, result returns + included.
  - Edge case: worker requests un-scoped tool → `ToolScopeError`; budget ledger reconciles.
  - Error path: worker budget exhausted → `HandoffResult(status="budget_exhausted")`, parent survives.

  **Verification:**
  - worker isolated (no parent context leak); budget ledgers reconcile; handoff = sole coupling.

  **Integration/regression test:** `tests/integration/test_subagent_handoff_e2e.py` — use the real `Spawner`: an
  orchestrator spawns an isolated worker with a scoped tool set. Assert the worker raises
  `ToolScopeError` when it tries to call a tool outside its scope, and that the orchestrator and
  worker budget ledgers reconcile after the handoff completes.

  **E2E contract rows:** #7 (orchestrator-worker delegation).

- [ ] **Unit 13: Autonomy gate + proposal store (L8 — before any auto-loop)**

  **Goal:** the global e-stop (off/assist/auto) + per-loop confirm gates + proposal store —
  constructor-required on every auto-loop so a loop literally cannot be built ungated. Lands
  BEFORE goal/distill/evolve (L8).
  **Requirements:** R5
  **Dependencies:** Unit 9 (trace, the seam before first auto-loop), Unit 6 (config)
  **Decision trace:** #20c, L8; research control-autonomy-config.md

  **Files:**
  - Create: `agentkit/control/autonomy.py` (`Autonomy` enum, `AutonomyGate`, `must_confirm`),
    `proposals.py` (`Proposal`, `ProposalStore`)
  - Test: `tests/control/test_autonomy_gate.py`, `test_gate_constructor_required.py`

  **Approach:**
  - `Autonomy(str, Enum)`: OFF/ASSIST(default)/AUTO. `AutonomyGate(initial, bus)`: `level`,
    `set(level, *, source)` (emits `AutonomyChanged`), `allows_auto()`, `requires_confirm()`.
    `must_confirm(gate, per_loop)` = `requires_confirm() or per_loop=="confirm-required"`.
  - `GateConfig`: `evolve_merge`/`distill_new_skill` default `confirm-required`.
  - **Constructor-required:** every auto-loop `__init__(..., gate: AutonomyGate)` — structural,
    cannot be built ungated. **This gate also interprets the permission `ask` state (T3)** — one
    autonomy axis governs both auto-loops and the per-tool `ask` crossing.
  - `Proposal`/`ProposalStore.hold/decide` for confirm-required flows.
  - `validate_self_edit` + `SELF_EDIT_FORBIDDEN = {autonomy, gates}` (#24 e-stop integrity).

  **Patterns to follow:**
  - research control-autonomy-config.md; lands at `trace store → [GATE] → goal` seam.

  **Test scenarios:**
  - Happy path: assist → auto-loop proposes, user confirms via proposal store.
  - Edge case: autonomy=off → all auto-loops halt; `ask` tool → deny.
  - Error path: attempt to construct an auto-loop without a gate → fails at construction.

  **Verification:**
  - no auto-loop constructible ungated; off halts everything; self-edit of autonomy rejected.

  **Integration/regression test:** `tests/integration/test_autonomy_gate_e2e.py` — assert constructing a real auto-loop
  without an `AutonomyGate` raises at build time; with a gate set to `autonomy=off`, run the loop and
  assert it halts before any self-continuation; and assert a tool attempting to self-edit the
  `autonomy` field is rejected by the gate.

  **E2E contract rows:** #14 (autonomy=off halts goal/distill/evolve).

- [ ] **Unit 14: Full AgentConfig (layering, env interpolation, ComponentSpec 2-phase)**

  **Goal:** complete the config SSoT — layered sources, `${ENV}`/`env_key` secret indirection,
  ComponentSpec 2-phase validation — upgrading the Unit 6 skeleton.
  **Requirements:** R6
  **Dependencies:** Unit 6 (skeleton), Unit 13 (autonomy field), Unit 4 (provider params)
  **Decision trace:** #13, #26, #32, M-secret; research control-autonomy-config.md

  **Files:**
  - Modify: `agentkit/control/config.py` (full `AgentConfig`, `from_yaml`, `deep_merge`, resolver
    2-phase, `${ENV}` interpolation)
  - Test: `tests/control/test_config_layering.py`, `test_env_interpolation.py`, `test_no_plaintext_secret.py`

  **Approach:**
  - Layered: bundled → `~/.alfred/config.yaml` → `./agent.yaml` → env (`ALFRED_` prefix, `__`
    nesting) / code. `deep_merge` = recursive dict-merge, list/scalar replace, type-change →
    whole-node replace.
  - ComponentSpec 2-phase: parse-phase validates structure only (`{type:str,params:dict}`,
    extra=forbid, recursion); build-phase looks up `type` in registry, validates `params`
    against the component's `params_model`, recursing inner specs first. Unknown type →
    `UnknownComponentType`.
  - **`${ENV}` interpolation in ALL values incl. headers**; `env_key` stores env-var NAME;
    resolved at provider construction; missing → `MissingSecret` fail-loud; `extra=forbid`
    crashes on plaintext `api_key:` (KB `llm-prompt-and-boundary-contracts`).
  - All derived views (registries, enabled-plugin set) projected from `AgentConfig`, never
    hand-maintained (KB `ssot-drift`); frozen-mutate test.

  **Patterns to follow:**
  - research control-autonomy-config.md; Codex `[model_providers.x]` + env_key.

  **Test scenarios:**
  - Happy path: 3 layers merge, later overrides earlier; `${ENV}` resolves at construction.
  - Edge case: nested fusion spec resolves workers/judge before fusion (build-phase order).
  - Error path: plaintext `api_key:` → crash; missing `env_key` env var → `MissingSecret`.

  **Verification:**
  - layered merge correct; no plaintext secret possible; 2-phase validation catches bad type/params.

  **Integration/regression test:** `tests/integration/test_agentconfig_layering_e2e.py` — run real `AgentConfig.from_yaml`
  over 3 layered temp config files plus environment overrides. Assert `deep_merge` precedence
  (highest layer wins per key), that `${ENV}`/`env_key` references resolve from real env vars, and
  that a plaintext `api_key` in a file crashes at load.

  **E2E contract rows:** none directly (config mechanism; exercised by #6 fusion, #15 self-edit).

- [ ] **Unit 15: Goal subsystem (Codex /goal model + M5 no-progress)**

  **Goal:** first-class persistent goal state that self-continues at turn_end, bounded by SHA256
  no-progress detection + max-continuations (M5), governed by autonomy.
  **Requirements:** R4, R5
  **Dependencies:** Unit 13 (gate), Unit 9 (trace off-track), Unit 7 (turn_end), Unit 3 (session_start inject)
  **Decision trace:** #19, #20c, M5; research subsystem-goal.md

  **Files:**
  - Create: `agentkit/subsystems/goal/store.py` (per-thread JSON file `~/.alfred/goals/<thread_id>.json`
    — NOT a db, store-scope S6-narrowed), `driver.py` (turn_end continuation), `detector.py` (SHA256 no-progress)
  - Create: `agentkit/tools/goal.py` (`set_goal`) + bundled `set-goal` skill
  - Test: `tests/subsystems/test_goal_continuation.py`, `test_goal_no_progress.py`

  **Approach:**
  - Per-thread JSON file `goals/<thread_id>.json` (`goal_id`, `objective`, `status`, `token_budget`,
    `tokens_used`, counters); status ∈ active/paused/blocked/usage_limited/budget_limited/complete/no_progress; verbs
    set/view/pause/resume/clear; `set_goal` tool actions complete/update_progress/refine/block.
  - Inject at session_start; **drive at turn_end** (unmet & not paused → synthetic "continue
    toward: <objective>" through normal input path). NOT a turn_start poll.
  - **M5 (checked BEFORE budget):** (1) SHA256 no-progress detector — input-only fingerprint
    `SHA256(tool_name+serialized_args)` + turn-level hash; window 10, exact-repeat 3× or
    ping-pong → stuck; warn-then-block (`max_warnings_before_block=2` → 3rd = `no_progress`);
    `loop_exempt` tools excluded. (2) `max_self_continuations=25` (reset by real user msg).
  - Decision order: load → complete? → block? → autonomy off/assist → progress detector →
    max_self_continuations → budget → inject.

  **Patterns to follow:**
  - research subsystem-goal.md; Hermes #481 fingerprint.

  **Test scenarios:**
  - Happy path: goal unmet at turn_end → self-continues without user input → met.
  - Edge case: repeated identical tool calls → no_progress after warnings, halts.
  - Error path: autonomy=off → no self-continuation.

  **Verification:**
  - self-continues toward goal; halts on no-progress/max-continuations; respects autonomy.

  **Integration/regression test:** `tests/integration/test_goal_driver_e2e.py` — set an unsatisfiable goal in a real goal
  store (JSON file) and run the real driver loop. Assert the SHA256 no-progress detector flips the
  goal status to `no_progress` after repeated identical states and that self-continuation halts
  instead of looping forever.

  **E2E contract rows:** #8 (goal self-continues to completion), #21 (no-progress halt).

- [ ] **Unit 16: Fusion provider (composite, M6 timeout/quorum/fallback)**

  **Goal:** a composite `ModelProvider` calling N workers in parallel + aggregating, with M6
  resilience; cross-vendor capable. loop stays fusion-unaware.
  **Requirements:** R4, R7
  **Dependencies:** Unit 1 (ABC), Unit 4 (workers), Unit 14 (config recursion)
  **Decision trace:** #10, #11, M6; research subsystem-fusion.md

  **Files:**
  - Create: `agentkit/subsystems/fusion/provider.py` (`FusionProvider`, `FusionPolicy`),
    `aggregator.py` (`Aggregator` ABC, code rules, `LLMJudgeAggregator`)
  - Test: `tests/subsystems/test_fusion_quorum.py`, `test_fusion_toolcall_vote.py`

  **Approach:**
  - `FusionProvider(workers, aggregator, policy)` implements `complete()`; registers in `models`.
    Sub-providers + judge injected at construction (never loop).
  - `FusionPolicy(extra="forbid")`: `per_worker_timeout_s=30.0`, `quorum=1`,
    `on_quorum_fail="raise"`, `judge_failure="fallback_code"`, `code_fallback_rule="concat"`.
    Each worker `asyncio.wait_for` inside gather (swallow into `WorkerOutcome`, NOT TaskGroup);
    `enforce_quorum()` WARN per failure, `< quorum → FusionQuorumError`; judge failure → WARN +
    code fallback.
  - **Tool-call aggregation = vote the DECISION, never synthesize an action:** signature =
    `sorted([(name, canonical_json(args))])`, drop id; vote move-type then signature, return
    winner verbatim; LLM-judge in tool situations = pick-best `{choice:int}` only.
  - usage summed (cost honesty).

  **Patterns to follow:**
  - research subsystem-fusion.md M6 policy fields.

  **Test scenarios:**
  - Happy path: 2 workers (1 Anthropic + 1 OpenAI) vote, aggregator returns winner.
  - Edge case: one worker times out, quorum=2 unmet → `FusionQuorumError` clean model-error.
  - Error path: judge fails → code fallback (concat), no info dropped.

  **Verification:**
  - both vendors truly called; quorum/timeout/judge-fallback behave; tool-call vote verbatim.

  **Integration/regression test:** `tests/integration/test_fusion_provider_e2e.py` — run a real `FusionProvider` over two
  `MockProvider`s where one is deliberately slow/timeout. Assert quorum logic resolves the surviving
  responses, that a forced judge failure falls back to the documented code path, and that a
  tool-call vote returns one worker's response verbatim.

  **E2E contract rows:** #6 (forced cross-vendor fusion), #20 (worker timeout + quorum).

- [ ] **Unit 17: mcp client (tools-registry source)**

  **Goal:** connect mcp servers (stdio+HTTP), register their tools into the `tools` registry as
  normal entries (one dispatch path); freeze at session_start; anyio FILO teardown.
  **Requirements:** R4
  **Dependencies:** Unit 6 (registries/permission), Unit 14 (config env_key)
  **Decision trace:** #21; research mcp.md

  **Files:**
  - Create: `agentkit/mcp/register.py`, `manager.py` (`MCPManager`, `MCPServerConfig` union)
  - Test: `tests/mcp/test_mcp_register.py`, `test_mcp_teardown.py`

  **Approach:**
  - Discovered tools → `ToolDef`+handler entries (indistinguishable from local). Config key
    `mcp_servers`, discriminated union on `transport` (`StdioMCPParams`/`HttpMCPParams`,
    extra=forbid). HTTP auth via `headers_env_key` (env-var NAME, T7/secret hygiene).
  - **anyio FILO teardown:** single `AsyncExitStack` on one owning task, every transport +
    `ClientSession` via `enter_async_context`; `aclose()` unwinds FILO. Never per-server stacks.
  - Freeze at session_start (no `tools/list_changed`); connection failure → fail-loud naming
    server/command/url. Collisions → precedence (local > mcp-by-order > plugin) + WARNING.
  - mcp tools default permission per T6 baseline principle (external = ask by default).

  **Patterns to follow:**
  - research mcp.md; official `mcp` SDK.

  **Test scenarios:**
  - Happy path: stdio mcp server tool appears + is called + returns real result.
  - Edge case: tool name collides with local → local wins + WARNING.
  - Error path: server connect fails → fail-loud naming it; teardown across tasks no RuntimeError.

  **Verification:**
  - mcp tools in one dispatch path; FILO teardown clean; secrets via env_key.

  **Integration/regression test:** `tests/integration/test_mcp_manager_e2e.py` — launch a real tiny stdio MCP server as a
  subprocess and connect to it via the real `MCPManager`. Assert its advertised tool registers in
  the tool registry and is actually callable through the manager, and that FILO teardown across
  multiple tasks completes with no `RuntimeError`.

  **E2E contract rows:** #13 (mcp tool appears + called).

- [ ] **Unit 18: distill (trace2skill, parallel fleet, idle/tick)**

  **Goal:** mine batches of traces in parallel → propose conflict-free skills via prevalence
  merge; gated confirm-required; subscribes idle/tick (NOT turn_end).
  **Requirements:** R4
  **Dependencies:** Unit 9 (trace read), Unit 11 (skill writer M4), Unit 13 (gate), Unit 16-era provider
  **Decision trace:** #18, #32 (arxiv 2603.25158); research subsystem-distill.md

  **Files:**
  - Create: `agentkit/subsystems/distill/miner.py` (fleet), `merge.py` (prevalence), `events.py`
  - Test: `tests/subsystems/test_distill_merge.py`, `test_distill_gate.py`

  **Approach:**
  - Subscribe idle/tick. Select diverse batch (`batch_min=50`) → parallel analyst fleet (𝒜⁺
    success single-pass, 𝒜⁻ error ReAct) bounded by `asyncio.Semaphore` (W=8-16) → patches
    `{file,op,anchor,content}` → 3-layer conflict-free merge (programmatic reject dangling/
    collisions; inductive prevalence keep-recurring; catalog identity route same-root → deepen
    via M4). High-water mark = last-mined trace id.
  - Emit `distill.proposed`/`distill.written`; new-skill gate confirm-required (#20c). **High-water
    mark (last-mined trace id) + proposal queue persisted in the existing `trace.db`** (small meta
    tables — NOT a new db; store-scope S6-narrowed) so a daemon restart never re-mines or skips traces.
  - Closed-loop: proposals parsed/validated, not self-reported (KB `closed-loop-gate`).

  **Patterns to follow:**
  - research subsystem-distill.md; arxiv 2603.25158 prevalence merge.

  **Test scenarios:**
  - Happy path: batch of traces → proposed skill → confirm → lands in highest-precedence root.
  - Edge case: conflicting patches → prevalence keeps recurring, drops idiosyncratic.
  - Error path: gate=confirm → no write until user accepts.

  **Verification:**
  - parallel mining, conflict-free merge, gated write.

  **Integration/regression test:** `tests/integration/test_distill_e2e.py` — seed a real `trace.db` with a batch of traces
  and run the real distill pass. Assert it produces a proposal that is gated (nothing written to the
  skill store until accept) and that the distill high-water mark persisted in `trace.db` survives a
  process restart (re-running distill does not reprocess sealed traces).

  **E2E contract rows:** #9 (distill proposes → confirm → new skill lands).

- [ ] **Unit 19: evolve (oracle-mutator-selector, M4, score_rollouts)**

  **Goal:** mutate skills with trace → score variants on replay set (success+failure) → keep
  best + version; merge gated; shares `score_rollouts()` with eval.
  **Requirements:** R4
  **Dependencies:** Unit 9 (replay_set), Unit 11 (writer M4), Unit 13 (gate), and the shared
  `score_rollouts()` helper. Implementation-order correction: `score_rollouts()` is owned by
  eval-harness (Unit 23), not Unit 24; either run Unit 23 before Unit 19 or extract a small shared
  scorer micro-unit before both evolve and eval.
  **Decision trace:** #18a, #20a, #20b, M4 (arxiv 2605.21810); research subsystem-evolve.md

  **Files:**
  - Create: `agentkit/subsystems/evolve/engine.py` (oracle/mutator/selector), `types.py`
    (`Lesson`, `LessonBank`, `Candidate`, `SelectMetrics`)
  - Test: `tests/subsystems/test_evolve_select.py`, `test_evolve_revert.py`

  **Approach:**
  - `skill_used` accumulation enqueues candidate; run on idle/tick (batch). Filter gates
    cheap→expensive: permission (`origin=="human"` protected) → `min_replay_rows` → failure-signal
    presence; missing = silent DEBUG skip (#20b).
  - `SelectQ(S) = -1 if blocked else PassRate(S)+ε·U(S)`; `U=0.60·F_LCB+0.20·F̄_progress+
    0.20·Q_skill`; `F_LCB=max(0, F̄_progress − 1.96·σ/√R)`, R≈4. Re-rollout (preferred) or
    replay-judge fallback. **Reuse `score_rollouts()` from eval (Unit 23), not re-implemented.**
  - M4: `SkillStoreWriter.commit_version` (lock + whole-dir archive → `.versions/vN/` + os.replace
    + `manifest.json` last as commit point; `origin=evolve`). Lesson-bank + candidate metrics +
    pass-rate persist in `.versions/<v>/lesson_bank.json` + manifest (files, store-scope S6-narrowed —
    skills never touch a DB). Acceptance: PassRate(survivor) > active by ≥1 row or ≥δ. Merge gate
    confirm-required; `revert` = copy `.versions/vN/` whole dir back.

  **Patterns to follow:**
  - research subsystem-evolve.md; arxiv 2605.21810; DGM upgrade seam (not built).

  **Test scenarios:**
  - Happy path: skill with trace → variant scored higher → merge (gated) → version +1.
  - Edge case: skill without trace → silent skip; human skill protected by default.
  - Error path: `revert` restores prior version (old version still loadable).

  **Verification:**
  - variant scoring via shared scorer; gated merge; revert works.

  **Integration/regression test:** `tests/integration/test_evolve_e2e.py` — take a real skill with a recorded trace replay
  set and run the real evolve pass. Assert a variant is scored via `score_rollouts`, that a gated
  merge bumps the skill version by one, and that a revert restores the prior skill directory byte-
  for-byte.

  **E2E contract rows:** #10 (evolve variant → merge → version+1 → revert).

- [ ] **Unit 20: dream (memory janitor, archive-not-delete)**

  **Goal:** memory housekeeping only (dedup/merge/re-index/decay); reads session/trace, writes
  memory; never touches skills; archives instead of hard-deleting.
  **Requirements:** R4
  **Dependencies:** Unit 10 (memory), Unit 9 (trace read), Unit 13 (gate)
  **Decision trace:** #18; research subsystem-dream.md

  **Files:**
  - Create: `agentkit/subsystems/dream/janitor.py`, `events.py`
  - Test: `tests/subsystems/test_dream_dedup.py`, `test_dream_no_skill_touch.py`

  **Approach:**
  - Subscribe idle (broad) + session_end (scoped). Ops: dedup (`cos≥0.95` auto, `0.85≤cos<0.95`
    LLM judge), merge (`0.80≤cos<0.95`, cluster ≤5), re-index, decay (retention score → archive
    to `facts/.archive/` if `retention<threshold AND days>ttl_floor`). Guard: `dream_running` +
    `rerun_requested` coalesce + single async lock. Emit `dream.consolidated`.
  - **Structurally NOT given skill store/loader** (DI enforces memory-only boundary). Archive,
    never hard-delete; merge inputs archived (reversible). Watermark `last_dream_pass`.

  **Patterns to follow:**
  - research subsystem-dream.md; gen-agents retention decay.

  **Test scenarios:**
  - Happy path: redundant facts merged after dream; skills untouched.
  - Edge case: gray-band similarity → LLM judge decides.
  - Error path: decayed fact archived (recoverable), not deleted.

  **Verification:**
  - memory tidied; skills provably untouched (no skill-store dep injected); archive reversible.

  **Integration/regression test:** `tests/integration/test_dream_e2e.py` — populate a real memory store with redundant and
  stale facts and run the real dream pass. Assert redundant facts are merged, decayed facts are moved
  into `facts/.archive/` (not deleted), and that the skill store is provably untouched (no skill
  dependency injected and skill file hashes unchanged).

  **E2E contract rows:** #16 (memory tidied, skills untouched).

### Milestone D — Consumers (eval-harness + server/SSE)

- [ ] **Unit 21: Server shell + SSE outlet + replay-script (zero-rework TUI guarantee)**

  **Goal:** `agentkit-server` — a thin consumer that serializes the SAME event stream to SSE
  (`GET /events`), plus the minimal replay script that proves granularity sufficiency.
  **Requirements:** R1, R8
  **Dependencies:** Unit 2 (event-bus `serialize()`/`stream()`), Unit 7 (loop)
  **Decision trace:** #3, #15, #25; research (event-bus)

  **Files:**
  - Create: `agentkit-server/` package (`app.py` SSE endpoint, `GET /events`), `replay.py`
    (reference consumer / verification tool)
  - Test: `tests/server/test_sse_stream.py`, `test_replay_matches_cli.py`

  **Approach:**
  - server is a CONSUMER of core (zero core changes): `bus.stream()` → SSE frames via the
    generic `{type, payload}` rule (#9, same serialization as CLI stream-json J2 — one SSoT).
    `stream_delta` opt-in for token rendering.
  - `replay.py` (~tens of lines, NOT a TUI): consumes the SSE stream and reconstructs the full
    turn in plain text (user input + token reply + tool name/args/result). Objective pass: the
    reconstruction MATCHES direct CLI `text` output → granularity sufficient (e2e #12). Shares
    parse logic with CLI stream-json consumer (cli-json-output J4).

  **Patterns to follow:**
  - Decision #25 replay-script-matches-CLI contract; opencode backend/SSE split.

  **Test scenarios:**
  - Happy path: SSE client receives all events for one turn; replay reconstructs it.
  - Edge case: reconstructed text matches CLI output byte-for-relevant-field.
  - Error path: a missing field → reconstruction mismatch → fails (the contract working).

  **Verification:**
  - SSE carries enough to render; replay output matches CLI; core untouched.

  **Integration/regression test:** `tests/integration/test_server_sse_replay_e2e.py` — start the real `agentkit-server`,
  `curl -N` the SSE `/events` stream for one turn capturing the event log, then run the real replay
  script over that log. Assert the reconstructed text matches the text produced by running the same
  turn through the direct CLI.

  **E2E contract rows:** #12 (SSE replay reconstructs turn matching CLI).

- [ ] **Unit 22: Cron daemon (fresh session per tick, headless host)**

  **Goal:** the scheduler producer + the server-as-daemon host that runs cron jobs unattended,
  fresh session per tick, writing output.
  **Requirements:** R4
  **Dependencies:** Unit 21 (server host), Unit 8 (session), Unit 13 (autonomy)
  **Decision trace:** #6, #22, H3; research (kernel-context-cache H3)

  **Files:**
  - Create: `agentkit-server/scheduler.py` (sleep-until-deadline producer, emits `tick`/`job_due`),
    `cron.py` (job runner)
  - Test: `tests/server/test_scheduler.py`, `test_cron_fresh_session.py`

  **Approach:**
  - Scheduler = producer (sleep-until-deadline, NOT 60s poll); emits `tick`/`job_due` (Push,
    not poll). Host = whoever holds the asyncio loop: server shell is the REQUIRED daemon host
    for headless cron/dream (honest dependency, #6).
  - Cron tick = fresh session per tick (H3: a session = one frozen-prefix epoch; no cache
    penalty, new skills load free). `cron/jobs.json` config; output → `cron/output/<job>/<ts>.md`.

  **Patterns to follow:**
  - research kernel-context-cache.md H3 (cron fresh-session); Hermes cron = fresh session/tick.

  **Test scenarios:**
  - Happy path: job due → runs unattended → output file appears with real result.
  - Edge case: each tick is a fresh session (no cross-tick context bleed).
  - Error path: job failure logged, daemon survives, next tick proceeds.

  **Verification:**
  - cron runs headless; fresh session per tick; output written.

  **Integration/regression test:** `tests/integration/test_cron_e2e.py` — start the real server daemon with a cron job
  configured to fire immediately. Assert a `cron/output/<job>/<ts>.md` file appears containing the
  real run result and that two consecutive ticks each run in a fresh session (distinct session ids,
  no carried-over conversation state).

  **E2E contract rows:** #11 (cron job runs unattended, writes output).

- [ ] **Unit 23: eval-harness (consumer package, shared score_rollouts)**

  **Goal:** `agentkit-eval` — a consumer that starts whole agents to A/B configs; the
  self-measuring bench (CEO 10th star); owns `score_rollouts()` shared with evolve.
  **Requirements:** R9
  **Dependencies:** Unit 7 (Agent facade), Unit 14 (AgentConfig), Unit 9 (trace = eval record)
  **Decision trace:** #31, #32; research eval-harness.md

  **Files:**
  - Create: `agentkit-eval/` package: `types.py` (`Task`,`Score`,`Arm`,`Experiment`,`Rollout`),
    `runner.py` (`run_experiment`), `scorer.py` (`Scorer` protocol + builtins), `aggregate.py`
    (`score_rollouts()` + `F_LCB` — the shared surface)
  - Test: `tests/eval/test_run_experiment.py`, `test_parity_guard.py`, `test_score_rollouts.py`

  **Approach:**
  - Consumer (NOT Ring-3): drives the public SDK `Agent`/`agent.run()` like the CLI; a
    subsystem can't spawn loops. Core (`runner`+`scorer`+`aggregate`) ~120 lines.
  - `Experiment(name, arms≥2 [arm0=baseline], task_set, scorer, repeats=5, seed=0)`; `validate()`
    parity guard (deep-diff arms vs baseline, differing keys ⊆ declared `varies:` → else crash =
    eval analogue of `extra=forbid`).
  - `score_rollouts()` + `F_LCB` in `aggregate.py` = the explicit DRY surface evolve (Unit 19)
    imports, not re-implements. Fresh `Agent(arm.config)` per rollout (no leak); cost from
    provider `Usage` never estimated; paired CI gates headline claim.
  - **Closed-loop gate (KB `closed-loop-gate`):** score by parsing the artifact vs setpoint,
    not agent self-report. "kernel-only" arm = Tier-0 (Ring-3 absent / `{type:none}`, autonomy off).

  **Patterns to follow:**
  - research eval-harness.md; evolve shares `score_rollouts()` (Unit 19).

  **Test scenarios:**
  - Happy path: 2-arm experiment (kernel-only vs +subsystem) → per-arm scores + cost delta.
  - Edge case: arms differ on >1 axis not in `varies:` → parity guard crashes at load.
  - Error path: a rollout fails → recorded, doesn't poison the arm's other rollouts.

  **Verification:**
  - A/B via config; shared scorer with evolve; parity guard; cost from real Usage.

  **Integration/regression test:** `tests/integration/test_eval_harness_e2e.py` — run the real `alfred eval run` over a
  2-arm experiment (kernel-only vs +subsystem) using `MockProvider`-backed tasks. Assert per-arm
  scores and a cost delta are produced, and that the parity guard crashes when the two arms differ
  on more than one axis.

  **E2E contract rows:** #29-adjacent — eval is itself observable via `alfred eval run`
  (a future e2e row; not in the carried #1-#29 set — note as a follow-up, do not invent a row here).

### Milestone E — Bundled Skills + Self-Edit

- [ ] **Unit 24: Bundled skills + `alfred-agent` self-edit + wayne-* ingestion**

  **Goal:** ship the `bundled` skill root — bootstrap meta-skills + `alfred-agent` (edits own
  config, restart-to-apply, autonomy field forbidden) + snapshot-copied wayne-* skills.
  **Requirements:** R4, R5
  **Dependencies:** Unit 11 (skill loader), Unit 14 (config self-edit), Unit 13 (autonomy guard)
  **Decision trace:** #24; research control-autonomy-config.md

  **Files:**
  - Create: `agentkit/bundled/skills/` (`create-skill`, `use-memory`, `spawn-worker`/`handoff`,
    `set-goal`, `alfred-agent`), `agentkit/tools/edit_config.py` (`edit_own_config`)
  - Create: snapshot copy mechanism for `~/.claude/skills/wayne-*` → `bundled` (manual re-copy)
  - Test: `tests/bundled/test_alfred_agent_self_edit.py`, `test_autonomy_field_forbidden.py`

  **Approach:**
  - Bundled root ships bootstrap meta-skills (Claude Code SKILL.md frontmatter compatible, #24)
    + `alfred-agent` (edits `AgentConfig` file). Edits take effect on **daemon/CLI restart**
    (frozen-config semantics); governed by autonomy gate; **`autonomy`/`gates` fields FORBIDDEN
    for self-edit** (#24/#14 `SELF_EDIT_FORBIDDEN` — e-stop integrity).
  - wayne-* copied in as snapshot (NOT external root); trade-off recorded: updates don't
    auto-sync, manual re-copy.

  **Patterns to follow:**
  - research control-autonomy-config.md self-edit validation; #24.

  **Test scenarios:**
  - Happy path: agent calls `alfred-agent` to change model → restart → new model in effect.
  - Edge case: agent attempts to edit `autonomy` field → rejected.
  - Error path: malformed self-edit → fail-loud, config unchanged.

  **Verification:**
  - self-edit applies on restart; autonomy self-edit blocked; wayne-* ingested.

  **Integration/regression test:** `tests/integration/test_bundled_self_edit_e2e.py` — run the real bundled `alfred-agent`
  and have it self-edit its config to change the active model, then restart the process and assert
  the new model is in effect on the next turn. Then attempt a self-edit of the `autonomy` field and
  assert it is rejected.

  **E2E contract rows:** #15 (self-edit applies on restart + autonomy edit rejected).

## E2E Verification Contract

> Carried verbatim from spec §10; Status mutated only by wayne-verify. Format per
> `_shared/e2e-contract.md`. Rows #1-#17 are the spec contract; rows #18-#22 are the L9
> negative-path rows added per the Eng review (Decision #31, spec §13 L9); rows #23-#24 are
> the layered-instruction rows (layered-instructions decision log, spec §3.5); rows #25-#28 are
> the tools-permission rows (tools-permission decision log, spec §3.6); row #29 is the
> CLI JSON-output row (cli-json-output decision log, spec §3.7). e2e runs REAL
> LLMs across BOTH vendors (Anthropic + OpenAI/Azure via local env / key-proxy
> `127.0.0.1:8888`). Unit/integration tests in the units above are NOT this gate.
>
> **Real-LLM env sourcing (real, verified 2026-06-15).** Runtime E2E rows executed by
> `wayne-verify` load credentials from the real local files/env (do NOT hard-code or invent keys).
> Per-unit files under `tests/integration/` may use mocks/direct stores and are not this gate:
> - **Anthropic** ← `~/.claude/settings.json` `env` block for `ANTHROPIC_BASE_URL=http://127.0.0.1:8888`
>   and `ANTHROPIC_API_KEY`; model id from `ALFRED_REAL_MODEL` or current process
>   `ANTHROPIC_DEFAULT_*_MODEL`. The Claude settings file is the base-url/key source, not the model
>   source.
> - **OpenAI/Azure** ← `~/.codex/config.toml` `[model_providers.custom]`: `base_url=http://127.0.0.1:8888/openai`,
>   `wire_api="responses"` (**Responses API, not Chat Completions — confirms Decision #32 / e2e #1 risk**),
>   `query_params.api-version=2025-04-01-preview`, header `Ocp-Apim-Subscription-Key`, model `gpt-5.5`.
> - **Secret hygiene (Decision #32.4):** the real codex config inlines the subscription key as a
>   PLAINTEXT http header — Alfred MUST ingest it via `env_key`/`${ENV}` indirection, never copy the
>   plaintext value into an `agent.yaml`. The test harness reads these files, exports the secrets to
>   env, and Alfred config references them by env-var NAME.
> - **SSRF vs proxy boundary:** the `web_fetch` SSRF guard denies `127.0.0.1:8888` (a TOOL egress),
>   but the provider layer (LiteLLMProvider `base_url`) legitimately reaches the same proxy — two
>   different egress paths; the SSRF denylist governs the tool only, never the provider.
> - **Verify profile vs local regression:** normal `uv run pytest` runs unit/integration tests and
>   skips live-provider tests by default. `wayne-verify` runs `ALFRED_RUN_REAL_E2E=1 uv run pytest
>   tests/e2e` plus any row-specific setup, and must treat rows #1/#17/#29 as non-skippable:
>   Anthropic and OpenAI/Azure both run, row #1 executes a real CLI hashread tool call, row #17
>   proves cache hits, row #29 proves stream deltas and replay. Every file under `tests/e2e/`
>   must issue at least one real LLM call.

| # | User path | Env: process | Env: data | Env: entrypoint | Observable (pass = ?) | Status |
|---|-----------|--------------|-----------|-----------------|----------------------|--------|
| 1 | Dev starts CLI, asks a question; agent calls a read-file tool and answers with the file content (run TWICE: Anthropic, then OpenAI) | `alfred chat` (in-process) | real `hello.txt` | `alfred` CLI | terminal prints the real content of `hello.txt`; tool call visible; **both vendors pass**. Test must force or prove `hashread` was called, not merely answer from prompt text. | ⬜ |
| 2 | Dev does `import alfred` in a script, builds an agent, runs one turn, gets a result object | `uv run script.py` (in-process) | tmp dir | Python `import` | stdout shows the returned response object with complete fields (message + tool trace) | ⬜ |
| 3 | Dev has two conversations; second asks "what did I just ask", agent recalls | `alfred chat --continue` | real `sessions.db` | `alfred` CLI | turn-2 answer correctly restates turn-1 content (FTS5 recall) | ⬜ |
| 4 | Dev drops a skill into `./skills`; agent uses it in a fitting scenario | `alfred chat` | `./skills/<a-skill>/SKILL.md` | `alfred` CLI | skill visibly loaded (L0) and adopted in conversation; behavior matches the skill's description | ⬜ |
| 5 | Dev puts a same-named skill in two roots, starts up | `alfred chat` | two roots, same-name SKILL.md | `alfred` CLI | startup log prints WARNING: higher-precedence root wins, names the shadowed one | ⬜ |
| 6 | Dev configures model = fusion (ensemble vote), asks a question — **forced cross-vendor** | `alfred chat` | `agent.yaml` with fusion (1 Anthropic + 1 OpenAI worker) | `alfred` CLI | answer produced; trace/log shows BOTH vendors truly called + aggregator voted across both outputs | ⬜ |
| 7 | Dev has agent delegate a worker subtask (orchestrator-worker) | `alfred chat` | — | `alfred` CLI | terminal shows worker spawned, runs in isolation, result returns to orchestrator; final answer includes worker output | ⬜ |
| 8 | Dev sets a goal; agent doesn't finish in one turn and self-continues until met | `alfred chat` (`/goal set ...`) | — | `alfred` CLI | agent auto-continues after turn_end WITHOUT new user input; final goal-met state visible | ⬜ |
| 9 | After enough tasks, distill proposes a new skill; dev confirms; it lands | `alfred` daemon | real trace store | `alfred` CLI / server | proposal appears → confirm → new SKILL.md appears in highest-precedence root reflecting the distilled flow | ⬜ |
| 10 | Dev triggers evolve on a skill that has trace; reviews variant; merges | `alfred` daemon | skill-with-trace + trace store | `alfred` CLI / server | variant proposed → after merge skill version +1; `revert` restores old version (old version still loadable) | ⬜ |
| 11 | Dev configures a cron job; daemon runs it unattended and writes output | `alfred-server` daemon | `cron/jobs.json` | server daemon | at due time `cron/output/<job>/<ts>.md` appears with real execution result | ⬜ |
| 12 | Dev connects an SSE client, subscribes to one full conversation, receives all events needed to render a TUI | `alfred-server` on :PORT | — | SSE client (`curl -N`) | a **minimal replay script** consumes the stream and reconstructs the full turn in plain text (user input + token-by-token reply + tool name/args/result); reconstructed text **matches direct CLI output** | ⬜ |
| 13 | Dev connects an mcp server; its tool appears in agent's tools and is called | `alfred chat` | `agent.yaml` with mcp | `alfred` CLI | mcp-exposed tool appears in tool list, is successfully called in conversation, returns real result | ⬜ |
| 14 | Dev sets autonomy = `off`; agent's auto-loops (goal/distill/evolve) all halt | `alfred chat` | `agent.yaml` autonomy=off | `alfred` CLI | after off, goal no longer self-continues; distill/evolve don't trigger (log shows gate blocked) | ⬜ |
| 15 | Dev has agent call `alfred-agent` skill to edit its own config; restart applies | `alfred chat` → restart | `agent.yaml` | `alfred` CLI | after restart new config in effect (e.g. model changed); agent's attempt to edit `autonomy` field is rejected | ⬜ |
| 16 | Dev runs dream: after several sessions, memory is tidied (dedup/merge) | `alfred` daemon | real memory + session store | `alfred` CLI / server | after dream, memory file visibly tidied (redundant entries merged); **skills untouched by dream** | ⬜ |
| 17 | Dev runs two turns in one session; verify turn-2 hits prompt cache (Anthropic + OpenAI each) | `alfred chat --continue` | real provider (each vendor once) | `alfred` CLI | turn-2 API usage shows cache-hit > 0 (`usage.prompt_tokens_details.cached_tokens` via LiteLLM) ≈ frozen-prefix tokens; turn-1 may show either cache creation or a warmed read depending on prior proxy state, but the follow-up turn must show read hits | ⬜ |
| 18 | (L9 neg) A background subscriber raises during an async event; the loop survives and the error is visible | `alfred chat` (a deliberately-throwing test subscriber on `turn_end`) | — | `alfred` CLI | loop completes the turn; a `subscriber.error` event is emitted AND logged with the handler name; no crash | ⬜ |
| 19 | (L9 neg) Iteration budget is exhausted mid-turn (incl. under a subagent); agent stops cleanly | `alfred chat` with a tiny `budget.total_cap` | — | `alfred` CLI | `budget_exhausted` fires; loop returns a clean budget-stop message (not a stack trace); ledger reconciles (`total_remaining + Σledgers == total_cap`) | ⬜ |
| 20 | (L9 neg) A fusion worker times out; quorum logic decides the outcome | `alfred chat` | `agent.yaml` fusion with one worker pointed at an unreachable/slow endpoint, `quorum: 2` | `alfred` CLI | slow worker hits `per_worker_timeout_s`, logged WARNING; with quorum unmet a `FusionQuorumError` surfaces as a clean model-error (not a hang); with quorum met, vote proceeds on survivors | ⬜ |
| 21 | (L9 neg) A goal cannot make progress; it stops instead of looping forever | `alfred chat` (`/goal set <unsatisfiable>`) | — | `alfred` CLI | after repeated no-progress turns (SHA256 fingerprint repeat or `max_self_continuations`), goal status flips to `no_progress` and self-continuation halts; visible to the user | ⬜ |
| 22 | (L9 neg) A corrupt skill folder is present at startup; the agent skips it and runs | `alfred chat` | `./skills/<bad>/SKILL.md` (malformed YAML / missing name) + one good skill | `alfred` CLI | startup logs WARNING naming the bad skill (catalog error, skipped); the good skill still loads; agent runs normally (NOT a crash) | ⬜ |
| 23 | Dev writes a global rule in `~/.alfred/AGENTS.md` and a more-specific/conflicting rule in project `./AGENTS.md`, starts CLI and asks a question that triggers both | `alfred chat` | `~/.alfred/AGENTS.md` + `./AGENTS.md` (inside a git repo) | `alfred` CLI | agent behavior reflects BOTH layers loaded AND the nearest (project) layer winning on conflict; `-v` manifest lists both layers (path + char-count, included) | ⬜ |
| 24 | Dev declares an instruction source that points to an unreadable file (permission / missing), starts CLI | `alfred chat` | a declared-but-unreadable instruction path | `alfred` CLI | startup **fails loud naming the unreadable source** (NOT silently skipped, NOT silently empty instructions) | ⬜ |
| 25 | With autonomy=auto, agent tries a tool matching a `deny` pattern (e.g. `bash rm -rf`); it is blocked anyway | `alfred chat` | `agent.yaml` autonomy=auto + `bash:{"rm *":"deny"}` | `alfred` CLI | the dangerous call is **blocked despite full-auto** (deny is a hard wall); log names the tool + deny rule; agent continues without running it | ⬜ |
| 26 | An `ask`-tier tool is invoked interactively (confirm) and again headless (auto-deny) | `alfred chat` then `alfred-server` cron | `agent.yaml` with an `ask` tool | `alfred` CLI + daemon | interactive: user is prompted and on approve it runs; headless (no TTY): same call is **denied with a fail-loud log** ("ask downgraded to deny: no interactive channel") | ⬜ |
| 27 | Agent edits a file with `hashedit`, but the line changed since it was read; the stale edit is rejected | `alfred chat` | a real file mutated between read and edit | `alfred` CLI | `hashedit` **rejects the stale edit** (hash mismatch) rather than corrupting the file; agent re-reads and retries | ⬜ |
| 28 | Agent is induced to `web_fetch` an internal address; the SSRF guard blocks it | `alfred chat` | `web_fetch http://127.0.0.1:8888` (the key-proxy) | `alfred` CLI | the fetch is **denied by the SSRF denylist** (internal/metadata address); the key-proxy is never reached; log names the blocked address | ⬜ |
| 29 | Dev runs a turn with `--output-format stream-json` and reconstructs it offline | `alfred chat --output-format stream-json` | a question that triggers a tool call | `alfred` CLI | stdout is **valid JSONL** (one `{type,payload}` event per line) and includes `stream_delta` frames for token output; a minimal replay reconstructs the full turn (user input + token reply + tool name/args/result) matching `text`-mode output — symmetric with #12 SSE | ⬜ |

## Dead Code / Legacy Cleanup

- None. Greenfield repo (spec §11). No dead or legacy code exists; nothing to delete or
  deprecate.

## System-Wide Impact

- **Interaction graph:** one event stream (`EventBus.stream()`) feeds three consumers —
  CLI, server-SSE, Ring-3 hooks. Ring-3 plugins subscribe to kernel events and write only
  to existing stores; they never call the loop. fusion intercepts only the provider seam;
  middleware intercepts only loop steps. The autonomy gate is a constructor dependency of
  every auto-loop.
- **Error propagation:** tool exceptions → tool-result messages fed back to the model
  (loop never crashes). Blockable events (`pre_tool`) propagate the first raise as a veto.
  Background events isolate per-subscriber, emit `subscriber.error`, never crash the loop.
  Provider/LiteLLM exceptions map to Alfred-owned error classes. Config typos / missing
  secrets crash at startup (fail-loud). Corrupt skill = WARNING-skip (catalog error);
  unknown config key = crash (config error).
- **State lifecycle risks:** frozen-prefix cache discipline (no mid-session prompt
  mutation); epoch roll only at turn_end. Atomic skill writes (`os.replace` under per-skill
  lock); `manifest.json` written last as commit point; on-disk `SKILL.md` is SSoT for
  "what loads". Append-only trace JSONL must seal exactly once (`_sealed` flag + shielded
  finalizer). SQLite WAL with `BEGIN IMMEDIATE` + bounded retries for multi-writer.
- **Unchanged invariants:** the `ModelProvider.complete()` single-call interface the loop
  sees never changes regardless of fusion/router/single. Adding a kernel event stays a
  2-place edit. The handoff payload schema is the sole multi-agent coupling surface.

## Risks & Dependencies

### Known Risks (from past lessons)

| Lesson (KB path) | Trigger | Anti-pattern / prevention | Mitigation in this plan |
|------------------|---------|---------------------------|--------------------------|
| `how-to/llm-prompt-and-boundary-contracts.md` | designing system/user prompt split, changing prompt+parser together, pydantic-receiving external JSON | LLM seam lies 3 ways: attention (invariants drown), deploy-coupling (prompt+parser version together), input-trust (`extra=` must match source trust) | Unit 3 (context assembly): machine-parsed invariants in a small high-attention slot + byte-cap test. Units 4/14 (provider/config): `extra="ignore"` on LLM/skill input, `extra="forbid"` on owned `AgentConfig`. |
| `how-to/async-backend-contracts.md` | asyncio backend: async cleanup, timeout scope, append-only JSONL terminal events | terminal side-effect must land exactly once under cancel/timeout; `try/finally` alone insufficient; append emits not idempotent | Unit 2 (event-bus): per-subscriber isolation + own timeout. Unit 9 (trace store): seal terminal write behind `_sealed` flag + shielded finalizer + sync seal path. |
| `how-to/ssot-drift-and-schema-migration.md` | converging duplicated enum/dict/table to one SoT, or schema/identity migration | same state stored twice drifts; collapse by deletion + frozen derived projection (one writer); union-grep all callsites | Unit 14 (config): `AgentConfig` sole owner, all derived views projected; frozen-mutate test. Units 5/9 (session/trace): schema-version-guarded reader sweep, re-validate via pydantic on read. |
| `how-to/closed-loop-gate-for-llm-stage-output.md` | adding a verification gate to an LLM stage; output treated as authoritative un-verified | open-loop controller; verdict must come from objectively parsing the artifact, not the agent's self-report; `deviated` first-class | Unit 24 (eval harness): score by parsing the emitted artifact vs setpoint. Units 16/18 (distill/evolve): proposals parsed/validated, not self-reported; read-modify-write on trace records. |
| `how-to/external-cli-sdk-integration.md` (MEDIUM) | wrapping external CLI/SDK; parsing its stream | external contract is what you empirically verify, not what memory assumes; flattening structured stream drops blocks | Unit 4 (provider): probe LiteLLM's real streaming/tool-call/thinking delivery before freezing the ABC; decode by chunk type. |
| `how-to/parser-asymmetry-pitfalls.md` (MEDIUM) | changing a parser / new dispatch syntax | green tests cover imagined inputs; sequential regex re-matches; new form inherits positive but not negative rules | Unit 1 (loop tool-call parsing): single-pass parse, exact-match assertions, adversarial test inputs for malformed tool calls. |
| `how-to/incomplete-feature-coverage-protocol-and-schema-fields.md` (MEDIUM) | adding a Protocol/ABC hook, schema field, or widening a canonical set | multi-stage declare→implement→emit ships partially dead if a surface isn't wired | Unit 2 (event-bus): grep declare↔emit symmetry per event. Unit 14 (config): grep all call-sites per new defaulted field; derive operator-facing lists from canonical set. |

### Risks & Dependencies (technical)

| Risk | Mitigation |
|------|------------|
| LiteLLM kwarg/Azure Responses-API uncertainty (`api_base`/`extra_query`/`wire_api`) blocks e2e #1 | Pin at e2e #1 against the live `127.0.0.1:8888` gateway; isolate in `litellm_provider.py` only so a fix touches one file (Unit 4). |
| Concurrent budget decrement race under subagents | Await-free synchronous `reserve()`/`refund()`; two-counter reconcile invariant asserted in tests (Unit 1, e2e #19). |
| Async subscriber raises crash the loop | Background dispatch isolates per-subscriber + emits `subscriber.error` + re-entrancy guard (Unit 2, e2e #18). |
| Prompt-cache silently not hitting = 10× cost | Frozen-prefix discipline + usage-number assertion + runtime stuck-at-zero WARNING (Units 3/4, e2e #17). |
| Skill write torn read / multi-writer corruption | `os.replace` atomic swap under per-skill `asyncio.Lock`, single `SkillStoreWriter` owner, manifest written last (Units 11/16/18, e2e #10). |
| fusion worker hang / judge failure | `per_worker_timeout_s` + quorum + `judge_failure=fallback_code` (Unit 19, e2e #20). |
| goal runaway self-continuation | SHA256 no-progress detector + `max_self_continuations=25` checked before budget (Unit 20, e2e #21). |
| Headless cron/dream needs a living host | server shell is the required daemon host for those features (honest dependency, Decision #6); Units 22-23. |
| Secret leakage into config/headers | `env_key`/`${ENV}` indirection everywhere incl. headers; `extra="forbid"` crashes on plaintext `api_key:` (Unit 14). |

## Sources & References

- **Origin spec:** [docs/specs/2026-06-15-alfred-design.md](../specs/2026-06-15-alfred-design.md)
- **Decision log:** [docs/decisions/2026-06-15-hermes-agent-loop-decisions.md](../decisions/2026-06-15-hermes-agent-loop-decisions.md) (32 decisions)
- **Per-module research:** `docs/research/*.md` (17 files) — cited per unit.
- External: Hermes Agent (Nous Research); Trace2Skill distill arxiv 2603.25158; evolve
  arxiv 2605.21810; Codex `~/.codex/config.toml`; Anthropic Agent Skills / agentskills.io;
  LiteLLM prompt-caching docs.
