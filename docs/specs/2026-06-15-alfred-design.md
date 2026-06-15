# Alfred — Design Spec

Date: 2026-06-15
Status: design-approved (pending CEO + Eng review)
Repo: github.com/waynejing995/alfred
Decision log: ../decisions/2026-06-15-hermes-agent-loop-decisions.md (32 decisions)
Decision log (addendum): ../decisions/2026-06-15-layered-instructions-decisions.md (L1-L9, layered instructions → §3.5)
Decision log (addendum): ../decisions/2026-06-15-tools-permission-decisions.md (T1-T11, tool catalog + permission → §3.6)
Decision log (addendum): ../decisions/2026-06-15-cli-json-output-decisions.md (J1-J6, CLI json/stream-json output → §3.7)
Decision log (addendum): ../decisions/2026-06-15-store-scope-decisions.md (S1-S8, store scope + DB substrate → §3.8)

---

## Revision Recap

| Date | Change | Source | Section |
|------|--------|--------|---------|
| 2026-06-15 | Added §3.5 Layered instructions (persona/user `core/` + tree-walked, merged `AGENTS.md`); e2e rows #23 (layered merge + nearest-wins) #24 (instruction-source read-failure fail-loud). Filled the gap that §3.1 context-assembly left under-specified. | layered-instructions decision log L1-L9 | §3.5, §10 |
| 2026-06-15 | Added §3.6 Tool catalog + permission model (allow/ask/deny + pattern + per-agent narrow, composed with autonomy; permission pulled into Tier-0; 7-tool baseline = hashline read/edit + write_file + fff + list_dir + bash + web_fetch w/ SSRF guard; `allowed-tools` advisory→enforced; fff bundled per-platform). e2e rows #25-#28. Researched Codex/opencode/Hermes + oh-my-pi (hashline default). | tools-permission decision log T1-T11 | §3.6, §10 |
| 2026-06-15 | Added §3.7 CLI output formats (`--output-format text\|json\|stream-json`, reusing event-bus `serialize()` as SSoT; stream-json=JSONL realtime, json=aggregated terminal object). e2e row #29. Folded into Unit 7. | cli-json-output decision log J1-J6 | §3.7, §10 |
| 2026-06-15 | Added §3.8 Store scope: all stores under `~/.alfred/`, isolation via `project_id` column (not per-dir); global=memory core, per-project=session/trace/facts; project_id via shared L4a walk. **DB scope narrowed (S6/S9) to session/trace/facts only**; skill content+versions = pure files (`.versions/` + manifest, no DB), goal = JSON file, distill cursors = trace.db meta. S7/S8 (DB-active drift + hash-gate) deleted as the drift they fixed no longer exists. Revised Units 8-11, 15, 18, 19. | store-scope decision log S1-S6, S9 | §3.8, §4 |

## 1. What Alfred Is

A minimal, frontier-design **agent loop kernel + pluggable experiment bench**, shipped
**SDK-first** (Python, importable into other projects) with **UI strictly separated from
logic** (core has zero UI deps). It is a learning vehicle for agent design: the kernel is
tiny; every advanced capability (memory, skills, distill, dream, evolve, goal, fusion,
handoff, cron, mcp) is a swappable subsystem you can A/B to measure whether it actually
helps.

Guiding thesis (from KB `synthesis-harness-over-model`): **the harness, not the model, is
the dominant variable.** Therefore: keep the kernel small; make everything else an
experiment you can add/remove.

**Goal (Decision #1):** (B) minimal kernel + experiment bench, with (C) "maybe later"
production path. SDK-first, UI-separated.

---

## 2. Architecture — Three Concentric Rings + Three Packages

```
┌─ agentkit-cli ─┐  ┌─ agentkit-server (HTTP/SSE + cron daemon) ─┐   ← consumers
└───────┬────────┘  └────────────────────┬──────────────────────┘
        │  in-process import              │  over-the-wire (SSE)
        └──────────────┬──────────────────┘
                       ▼
        ┌──────────── agentkit (pure core) ────────────┐
        │  RING 1 KERNEL                                │
        │   loop · context-assembly · iteration-budget  │
        │   event-bus · 5 registries · ModelProvider-ABC│
        │  ─────────────────────────────────────────────│
        │  RING 2 STORES (interface + default impl)     │
        │   session · memory · trace · skill-loader     │
        │  ─────────────────────────────────────────────│
        │  RING 3 SUBSYSTEMS (plugins, event-driven)    │
        │   distill · dream · evolve · goal             │
        │   fusion (composite provider) · handoff       │
        │   + mcp (tools source)                        │
        └───────────────────────────────────────────────┘
```

**Packages (Decision #1, #22):**
| Package | Role | UI deps |
|---|---|---|
| `agentkit` | pure core (kernel/ stores/ subsystems/ mcp/) | none |
| `agentkit-server` | optional HTTP/SSE shell + cron daemon (a consumer; also the headless daemon) | none |
| `agentkit-cli` | thin CLI (a consumer) | CLI only |
| `agentkit-eval` | eval harness (a CONSUMER — starts whole agents to A/B configs; NOT Ring-3, since a subsystem can't spawn loops) (Decision #32) | none |
| *future TUI* | OpenTUI client over server SSE | **TODO (§12)** |

**Core mental model:** core has zero UI knowledge and zero HTTP knowledge. **One event
stream feeds three consumers** (CLI / server-SSE / Ring-3 hooks).

**Ring invariant:** Ring 3 works ONLY via hooks/events/registries exposed by Ring 1/2 —
it never injects logic inward.

---

## 3. Ring 1 — Kernel (not pluggable)

| Component | Responsibility | Key invariant |
|---|---|---|
| **loop** | input → assemble prompt → call provider → parse → dispatch tool → repeat | knows only single `ModelProvider.complete()`; unaware of fusion/Ring-3 |
| **context assembly** | per-turn message assembly + cache breakpoints | **frozen prefix at session_start** (memory retrieval results / skill L0 / persona frozen in); not mutated mid-session |
| **iteration budget** | cap tool calls per turn | parent/subagent share total cap, separate ledgers; refundable for `execute_code`-class |
| **event-bus** | emit/on/off, wildcard subscribe | self-describing pydantic events; adding an event = 2 edits; `on("*")` for UI |
| **5 registries** | tools / events / models / skill_sources / middleware | mechanism open, catalog converged; plugin = packaged registration |
| **ModelProvider ABC** | `complete(messages) -> response` | fusion/single/router all implement it; judge calls via injected provider |

### 3.1 Event bus (Decision #5, #7, #9)

Self-describing pydantic event classes; **no central event-name enum**. Generic
serialization `{type: event.name, payload: event.model_dump()}`. Wildcard subscription
(`on("*")`, `on("tool.*")`). Kernel events use bare names; plugin events use prefixed
names (`dream.consolidated`) and the emitter owns the schema; registering against a
reserved/foreign namespace fails loud.

**Verifiable invariant (regression test):** adding a new kernel event touches only the
event-definitions file + one emit call site — not a central enum, not the serialization
layer; UI unchanged via wildcard.

**Kernel events (initial set, payload = references + metadata only, not full bodies):**
`session_start`, `turn_start`, `pre_tool`/`post_tool`, `turn_end`, `session_end`,
`idle`, `tick`/`job_due`, `skill_used`, `budget_warning`/`budget_exhausted`,
`handoff` (#23).
Dispatch: **sync for blockable** (`pre_tool` — subscriber may veto by raising); **async
for background-trigger** (`turn_end`/`idle`/`session_end`).

### 3.2 Five registries (Decision #8)

`tools` (name+schema+handler; used by user/mcp/plugin) · `events` (lifecycle hooks) ·
`models` (providers — used by fusion) · `skill_sources` (folders — used by multi-folder
loading) · `middleware` (loop-step interceptor — intercept+transform: rewrite flowing
data / short-circuit a step / inject retry; does what observe+veto events cannot — a
confirmed extension surface, Decision #30a). Catalog converged: no speculative tables. **plugin = the packaged form of
calls into these registries** (not a 14th subsystem).

### 3.3 Provider layer (Decision #28)

Alfred defines its own `ModelProvider` ABC + its own message/response pydantic types.
**LiteLLM is the default implementation** (`LiteLLMProvider`), wrapped behind the ABC —
only that one file imports litellm. LiteLLM unifies Anthropic/OpenAI formats, streaming,
tool calling, exceptions; supports custom base_url/headers/Azure. Swapping lib later =
new XProvider, core untouched.

**Provider config format (Decision #26)** learns from Codex `~/.codex/config.toml`
`[model_providers.x]` structured blocks; **secrets via `env_key` indirection** (config
stores the env-var NAME, never plaintext key); supports `base_url`/`http_headers`/
`query_params` (proxy gateway).

### 3.4 Cache (Decision #21, #29) — a Ring-1 discipline, not a subsystem

System prompt assembled once at session_start and frozen; cache breakpoint at end of
static prefix; never mutated mid-session. The provider boundary layer injects per-vendor
cache mechanism: **Anthropic** `"cache_control":{"type":"ephemeral"}` on content block
(LiteLLM passthrough); **OpenAI** auto-caches ≥1024-token prefix (no annotation, prefix
stability only). Verification: §10 e2e #17 + runtime "cached_tokens stays 0" WARNING.

### 3.5 Layered instructions (decision log `2026-06-15-layered-instructions`)

Defines *what the system prompt is made of, where instructions are discovered, and how
they merge* — the detail §3.1 context-assembly left under-specified. "opencode-ized
Hermes": Hermes's clean tier separation + opencode's directory-walk + merge.

**Three independent instruction concerns, three owners, non-overlapping** (L2):

| Concern | Owns | Source | Scope |
|---|---|---|---|
| identity / persona | who the agent is | memory `core/persona.md` | global, frozen (≈ Hermes SOUL) |
| user profile | who the user is | memory `core/user.md` | global, frozen (≈ Hermes USER) |
| **project instructions** | this work-tree's rules | **`AGENTS.md` layered** | per-tree, discover+merge (≈ opencode rules) |

**Global root (L3):** all global state under `~/.alfred/` (sibling `config.yaml`,
`memory/`, `skills/`); global instructions = `~/.alfred/AGENTS.md`. Root path default
`~/.alfred/`, overridable by the `ALFRED_HOME` env var only (no second config-file override).

**Discovery + merge (L4, L4a, L5, L5a):** global `~/.alfred/AGENTS.md` added explicitly;
project layer walks up from CWD to git root (stop at git root; if `$HOME` reached with no
git root → CWD single-layer fallback; never above `$HOME`). Per directory: `AGENTS.md` >
`CLAUDE.md`, take ONE per layer; **cross-layer MERGE (concatenate), global first, nearest
last** (nearest wins on conflict). `~/.claude/CLAUDE.md` NOT recognized (wrong audience).

**Freeze + position (L4b, L6):** discover+merge ONCE at session_start, freeze into the
prefix; edits take effect next session / on epoch-roll. Frozen order = `persona → user →
project_instructions → memory(facts) → skill_l0` (most-stable first, max cache hit).

**Budget + failure (L7):** char cap (default ~20k, configurable) is a **soft reminder
threshold, NOT a hard limit** — over-cap = WARNING only, full content kept uncut (no
truncation; truncating would silently drop user-authored instructions). An instruction
issue NEVER blocks `skill_l0`/`memory` loading (independent per-segment budget). No
AGENTS.md = DEBUG (normal). A *declared* source that can't be read = **fail-loud**.

**Observability + disable (L9-F1, L8):** session_start emits a **DEBUG-only resolved-
instruction manifest** (ordered layers: path + char-count + included/skipped, under `-v`);
the read-failure + over-cap WARNINGs stay default-visible. `instructions.enabled` config
toggle (default true) for eval A/B. Verification: §10 e2e #23, #24.

### 3.6 Tool catalog + permission model (decision log `2026-06-15-tools-permission`)

The `tools` registry mechanism (§3.2, §5.4) defines *how* tools register; this section
defines *the built-in catalog* and *the permission model* governing every tool call —
both previously unspecified. **Permission is an L0 dispatch-level invariant, in Tier-0**
(woven into `_dispatch`), not a late subsystem (T2 — corrects the original build order that
placed goal before any permission design).

**Permission model (T1, opencode-style):** single axis, three states **allow / ask / deny**
+ pattern-match (last-match-wins) + per-agent override. **Sandbox (capability boundary) =
TODO** (§12) — Alfred is SDK-first, sandbox is deployment-time.

**Composition with `autonomy` (T3) — two orthogonal axes, autonomy only interprets `ask`:**

| permission verdict | autonomy=off | autonomy=assist (default) | autonomy=auto |
|---|---|---|---|
| `allow` | run | run | run |
| `deny` | block | block | **block** (hard wall — auto cannot override) |
| `ask` | treat as deny | interactive confirm (headless → deny) | treat as allow |

Headless (no TTY): `ask`→deny unless auto, with fail-loud log. **deny is a user wall no
autonomy level breaks.**

**SSoT + merge (T9, T10):** permission declarable at 3 sites — `AgentConfig` base → skill
`allowed-tools` (now **enforced**, not advisory) → per-agent / handoff tool-scope (#23a).
**Each later layer may only narrow** (lattice `deny > ask > allow`, take strictest); no
layer can widen its own privilege.

**Built-in tool catalog baseline (T6) — 7 general tools** (beyond subsystem-owned
`skills_*`/`memory_*`/`spawn_subagent`/`handoff_to`/`set_goal`/`edit_own_config`/mcp):

| Tool | Role | Default permission |
|---|---|---|
| `hashread` | read file, emits `LINE:HASH\|content` | `read` = allow |
| `hashedit` | edit existing lines, hash-anchored (rejects stale) | `write` = ask |
| `write_file` | create / overwrite full file | `write` = ask |
| `fff` | frecency-ranked search (bundled binary) | `read` = allow |
| `list_dir` | list directory | `read` = allow |
| `bash` | shell command | ask; `rm *`/`sudo *`… = deny |
| `web_fetch` | HTTP GET | ask; internal/metadata addrs = deny |

**File tools = hashline (T4, T6a):** `hashread`+`hashedit` are a paired anchoring system
(read tags lines with content hashes; edit validates the hash unchanged → closes "what I
read == what I edit" by mechanism, not model recall). Validated baseline: default in
oh-my-pi (Can Bölük's Pi fork; benchmark lift e.g. Grok Code Fast 1 6.7%→68.3%); same
`harness>model` thesis as Alfred. Tools stay separate but `hashedit`+`write_file` share one
`write` permission bucket (opencode-proven).

**Search = `fff` (T4, T5):** bundled binary (Fast File Finder, bigram+mmap+SIMD+frecency
LMDB), invoked via subprocess (NOT a Python dep, same class as ripgrep). Shipped via
**per-platform companion packages** `agentkit-fff-<platform>`; fallback chain ripgrep →
python grep with fail-loud WARNING if the binary is absent/unsupported.

**`web_fetch` SSRF guard (T7):** first egress tool — default `ask`; hard deny on
`localhost`/`127.0.0.1`/`169.254.169.254`/private ranges (**protects the local key-proxy
`127.0.0.1:8888`**); fetched content marked untrusted on context injection.

**Observability (T11-F):** resolved per-tool permission (verdict + deciding layer) joins
the `-v` manifest; a deny/ask block logs a default-visible line. Verification: §10 e2e
#25-#28.

### 3.7 CLI output formats (decision log `2026-06-15-cli-json-output`)

CLI needs machine-readable output to test/debug. `alfred chat --output-format
text|json|stream-json` (J1): `text` = human default; `json` = whole turn aggregated into
one object (final message + tool trace + usage, `stream_delta` off); `stream-json` = one
JSON object per event as JSONL, realtime, `stream_delta` on (J3). **Format = event-bus
`serialize()` verbatim** (`{type, payload}`, #9) — NO new format, same SSoT as SSE so
CLI-JSON and SSE-JSON never drift (J2). CLI `stream-json` and the SSE replay-script (e2e
#12) are one consumer pattern over two transports (J4). Verification: §10 e2e #29.

### 3.8 Store scope + DB substrate (decision log `2026-06-15-store-scope`)

How Ring-2/3 state is located, scoped, and persisted — refines §4 which left it implicit.

**Location (S1):** all stores under `~/.alfred/` (global home, NOT in-project). **Isolation
= a `project_id` column + `WHERE project_id=?`** (S1a), NOT per-project dirs/db files — so
cross-project search is one toggled WHERE, one FTS5 index reused globally. (Claude Code uses
per-dir because its sessions are JSONL files; Alfred's are SQLite.)

**Scope (S2):** GLOBAL (no project_id) = memory `core/persona.md`+`core/user.md` (= the
layered-instructions L2 layers). PER-PROJECT (`project_id`) = session, trace, memory `facts/`.

**`project_id` (S3, S5):** normalized path of the project root, discovered via the SAME
layered-instructions L4a walk (git-root, cwd fallback) → ONE "project" definition shared by
instructions and stores. MVP accepts move/rename breakage; relink = TODO. Facts retrieval
defaults to current project (S4); merged project+global retrieval is a future WHERE-widen.

**DB scope — narrowed (S6, S9):** DB is used ONLY for retrieval-shaped / project-partitioned
data: **session, trace, memory facts**. Everything else uses the simplest medium:
- **skill content + versions = pure files** — active skill DIRECTORY (SKILL.md +
  `references/`/`scripts/`/`assets/`); `.versions/vN/` whole-dir snapshots (incl. ref/script);
  `manifest.json` = active pointer + history (`origin ∈ {human, distill, evolve, revert}`,
  parent, ts, lesson-bank, pass-rate). revert = copy `.versions/vN/` back; commit point =
  `manifest.json` written last after `os.replace`.
- **goal = a small JSON file** per thread (`~/.alfred/goals/<thread_id>.json`), no db.
- **distill high-water mark + proposal queue** = persisted in the existing `trace.db` (small
  meta tables, not a new db) so a daemon restart never re-mines or skips traces.

**On-disk SKILL.md is the single SSoT for "what loads"** — only one declaration of active, so
no db-vs-file drift exists by construction (the earlier DB version-ledger was dropped as
over-design; a hand-edit is natively the new active since the file IS the SSoT). Rationale:
for skill *versions* the managed object is itself a file directory, so a DB index over "which
dir is active" only adds a second SSoT and the machinery to repair it — KISS + Delete>Add.

---

## 4. Ring 2 — Stores (interface + default impl, swappable)

| Store | Default impl (MVP) | Swappable to | SSoT boundary |
|---|---|---|---|
| **session** | SQLite WAL + FTS5 | — | conversation record |
| **memory** | Letta-style files + retrieval (Decision #17a) | mem0 / Zep | cross-session facts; retrieval-based top-k injection at session_start (NOT dump-frozen) |
| **trace** | annotated execution trajectories (Decision #17) | — | agent-learning raw material (success/failure/user-pushback/off-track) |
| **skill-loader** | ordered multi-root scan + L0/1/2 (Decision #12) | — | skill identity = active version; `.versions/` invisible; same-name → higher-precedence wins + fail-loud WARNING |

### 4.1 Memory (Decision #17a)

`context window ≠ memory system`. MVP = files + retrieval (Letta "filesystem is all you
need", ~74% LoCoMo baseline, human-readable). Retrieval at session_start = top-k via
semantic+BM25+entity fusion. Swappable interface; mem0 (vector, weak temporal) and
Zep/Graphiti (temporal KG) are A/B alternatives.

### 4.2 Trace store (Decision #17) — first-class, separate from session

loop writes traces + annotations at `pre/post_tool` and `turn_end`. distill/evolve/dream
read here, NOT from session. Subagents write their traces here too. **trace = learning
raw material; session = conversation record — kept separate.**

### 4.3 Skill loader (Decision #12, #14, #20a-i)

Ordered roots = precedence (`./skills` > `~/.myagent/skills` > `bundled`). Identity =
frontmatter `name`. Same-name conflict → higher wins + WARNING listing shadowed. Skill
list frozen at session_start (no hot-reload — cache stability). L0 (name+desc) always in
prompt; L1 full SKILL.md on demand; L2 referenced files. Optional `skill_filter`
(`include_tags`/`exclude_tags` default empty + `disable:[name,...]` blacklist;
precedence: disable > tag > all-load). **Version archive (`.versions/`, dot-prefixed) is
invisible to the loader** — only the active version loads; history is revert-only backup.
**Format = compatible with Claude Code SKILL.md frontmatter** (Decision #24).

---

## 5. Ring 3 — Subsystems (plugins, event-driven)

All Ring-3: event subscribers + registry entries; independently disable-able & A/B; write
only to existing stores; call models via injected providers (never loop).

### 5.1 Same-context multi-model: fusion (composite provider) — Decision #10, #11

loop sees one `ModelProvider`; a fusion provider internally calls N providers in parallel
and aggregates. Registers into `models`. Shapes:

| Shape | Composition | Aggregator |
|---|---|---|
| ensemble/vote | N worker providers | code rule (vote/majority/rank) |
| router/dispatch | 1 router provider | forward |

**Aggregator** (Decision #11): pluggable — pure-code rule OR LLM-judge. Sub-providers
**injected at construction** (N workers + optional judge provider); LLM-judge calls via
its injected judge provider → depends only on `ModelProvider`, never loop. Aggregator is
a constructor param, not a 6th registry.

### 5.2 Multi-agent collaboration: handoff (extension of subagent) — Decision #23, #23a

handoff = multi-AGENT (independent context/loop/tools), control + structured payload
A→B. Built as an extension of subagent (S1), **not a new kernel primitive**. New pieces:
`handoff` event + payload schema + transfer record → trace store (feeds evolve).

| Built-in pattern | Control | Shape |
|---|---|---|
| orchestrator-worker | returnable | parent holds context, spawns isolated worker, result returns (puppeteer lives here) |
| sequential handoff | one-way | A completes → payload → B takes over |
| ~~swarm/peer~~ | — | NOT built-in; plugin seam |

**Isolation invariant (Decision #23a):** context / tool-permission / state isolation per
agent; workers don't auto-inherit parent toolset; communication only via explicit payload
+ `handoff` event — **the sole coupling surface.** All multi-agent variability converges
onto this one surface; future mode changes touch only it, never the kernel.

### 5.3 Other Ring-3 subsystems

| Subsystem | Hook | Read → Write | Calls model | Gate default |
|---|---|---|---|---|
| **subagent** (S1) | `spawn_subagent` tool | independent session → result to parent | yes | — |
| **distill / trace2skill** (S3) | `idle`/`tick` | batch traces → new skill (parallel, conflict-free) | yes | new-skill **confirm-required** |
| **dream** (S4) | `idle`/`session_end` | session/trace → **memory housekeeping ONLY** (dedup/merge/re-index/decay; does NOT touch skills) | yes | governed by global autonomy |
| **evolve** (S5) | `skill_used` accumulation | skills-with-trace → variant (mutate → score on trace replay → keep best + version) | yes | merge **confirm-required** |
| **goal** (S6) | `session_start` inject + `turn_end` self-drive | goal state (set/view/pause/resume/clear) | no (drives) | budget cap (safety net) |

**distill (Decision #18, paper 2603.25158):** parallel analyst fleet over a diverse
trajectory batch → trajectory-local lessons → conflict-free skill directory via
prevalence-based inductive merge. **dream vs distill:** hard split, peers, no mutual
calls; coordinate only via plugin events. **distill is batch/parallel over many traces,
NOT reactive per-turn.**

**evolve (Decision #18a, #20a, #20b, paper 2605.21810):** consumes trace failure/pushback
signals (not just usage). Oracle–mutator–selector MVP (variant → score on historical-trace replay set, mining
success AND failure modes → keep best + version it). Leaves room to upgrade to a
Darwin-Gödel archive (keep-ancestors, fitness-weighted) but MVP doesn't build it. Safety
= **versioning + revert** (option A: `.versions/` archive, NOT per-skill git); evolve may
modify distill- and evolve-produced skills by default; human skills protected by default,
configurable in TUI `/config`. **Having reasonable trace is a candidate FILTER** — skills
without trace silent-skip (DEBUG), NOT fail-loud; fail-loud reserved for true exceptions.

**goal (Decision #19):** Codex `/goal` model — first-class persistent state surviving
session/interruption; verbs set/view/pause/resume/clear; inject at session_start; **drive
at `turn_end`** (unmet & not paused → self-continue next turn), guarded by budget cap
(default high/off). NOT a turn_start poll.

### 5.4 mcp (Decision #21) — a tools-registry source

An mcp client connects to an mcp server and registers its tools into the `tools` registry
— indistinguishable from local tools to the loop (one dispatch path, SSoT). MVP: stdio +
HTTP transport; configured via `{type: mcp, params: {transport, command/url}}`.

---

## 6. Control & Configuration

### 6.1 Three-layer autonomy control (Decision #20c)

1. **Global `autonomy` switch**: `off` (all auto-loops halt) / `assist` (auto-loops
   require confirmation) / `auto` (full auto). **Default `assist`** = e-stop safety net
   governing ALL auto-loops (goal continuation / distill / evolve / dream).
2. **evolve merge gate**: default confirm-required.
3. **distill new-skill gate**: default confirm-required.

### 6.2 Unified config (Decision #13)

Single pydantic `AgentConfig` = SSoT. YAML/JSON primary load source; code-construction a
free SDK by-product (`AgentConfig.from_yaml(...)` and `AgentConfig(model=FusionConfig(...))`
— same schema). `extra="forbid"`. Layered: bundled → `~/.myagent/config.yaml` →
`./agent.yaml` → env/code. **Assembly-type components declared `{type, params}`
recursively** — fusion is the natural result of provider references nesting:

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
skill_sources: [./skills, ~/.myagent/skills, bundled]
```

### 6.3 Builtin skills (Decision #24)

`bundled` root ships: bootstrap meta-abilities (`create-skill`, `use-memory`,
`spawn-worker`/`handoff`, `set-goal`) + **`alfred-agent`** (lets agent edit its own
`AgentConfig`). **wayne-\* private skills copied into `bundled`** (snapshot; updates don't
auto-sync — manual re-copy). `alfred-agent` safety: edits take effect on **daemon/CLI
restart** (frozen-config semantics); governed by autonomy gate; **`autonomy` field
forbidden for agent to self-edit** (e-stop integrity).

---

## 7. Error Handling

- **Fail-Loud**: config typo → crash at startup (`extra=forbid`); trace-read failure /
  corrupt skill / model-call failure → raise; cache `cached_tokens` stays 0 across a
  `--continue` session → WARNING.
- **NOT fail-loud (filter conditions)**: evolve candidate without trace → silent skip
  (DEBUG). Mislabeling a normal "not triggered" as error is noise (signal-to-noise).
- **tool exceptions** → converted to tool-result messages fed back to the model, never
  crash the loop.

---

## 8. Build Strategy (Decision #22)

Kernel-first layered build; **v1 done-criterion = one end-to-end conversation** (loop + 1
tool + 1 real provider + in-process SDK call) to prove the kernel is alive in week 1.
Then attach subsystems in dependency order:

```
session → memory → skill(+multi-root) → subagent → trace store
   → distill / dream / evolve / goal → fusion → handoff → mcp → server-shell / cron
```

**Delivery discipline (Decision #27): 1 commit = 1 unit.** Each implementation unit (or
atomically-coupled group) is its own self-consistent commit, signed off as the human
(Jingwen Chen). Carried forward to wayne-plan / wayne-work.

---

## 9. Cybernetics Lens (Decision #20)

Applied (triggers all match). 6/8 principles satisfied by prior decisions. Two MEDIUM
findings surfaced and resolved: (#4 SoT) skill multi-writer → versioning+revert + origin
metadata (#20a); (#8 feedback stability) no global e-stop → three-layer autonomy (#20c).

---

## 10. E2E Verification Contract

SSoT for end-to-end verification. Format per `_shared/e2e-contract.md`. All Status = ⬜
(only wayne-verify flips). Observable = real user-visible outcome, never a transport
proxy. **e2e runs REAL LLMs, BOTH vendors (Anthropic + OpenAI/Azure)** (Decision #26).
Provider config reuses local env (`ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`; Azure via
key-proxy `127.0.0.1:8888`).

| # | User path | Env: process | Env: data | Env: entrypoint | Observable (pass = ?) | Status |
|---|-----------|--------------|-----------|-----------------|----------------------|--------|
| 1 | Dev starts CLI, asks a question; agent calls a read-file tool and answers with the file content (run TWICE: Anthropic, then OpenAI) | `alfred chat` (in-process) | real `hello.txt` | `alfred` CLI | terminal prints the real content of `hello.txt`; tool call visible; **both vendors pass** | ⬜ |
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
| 17 | Dev runs two turns in one session; verify turn-2 hits prompt cache (Anthropic + OpenAI each) | `alfred chat --continue` | real provider (each vendor once) | `alfred` CLI | turn-2 API usage shows cache-hit > 0 (`usage.prompt_tokens_details.cached_tokens` via LiteLLM) ≈ frozen-prefix tokens; turn-1 ≈ 0 | ⬜ |
| 23 | Dev writes a global rule in `~/.alfred/AGENTS.md` and a more-specific/conflicting rule in project `./AGENTS.md`, starts CLI, asks a question triggering both | `alfred chat` | `~/.alfred/AGENTS.md` + `./AGENTS.md` (inside a git repo) | `alfred` CLI | agent behavior reflects BOTH layers loaded AND the nearest (project) layer winning on conflict; `-v` manifest lists both layers (path + char-count, included) | ⬜ |
| 24 | Dev declares an instruction source pointing to an unreadable file (permission / missing), starts CLI | `alfred chat` | a declared-but-unreadable instruction path | `alfred` CLI | startup **fails loud naming the unreadable source** (NOT silently skipped, NOT silently empty instructions) | ⬜ |
| 25 | With autonomy=auto, agent tries a `deny`-pattern tool (`bash rm -rf`); blocked anyway | `alfred chat` | `agent.yaml` autonomy=auto + `bash:{"rm *":"deny"}` | `alfred` CLI | blocked despite full-auto (deny = hard wall); log names tool + rule; agent continues without running it | ⬜ |
| 26 | An `ask`-tier tool invoked interactively (confirm) and headless (auto-deny) | `alfred chat` then `alfred-server` cron | `agent.yaml` with an `ask` tool | `alfred` CLI + daemon | interactive: prompt → on approve runs; headless (no TTY): denied with fail-loud log ("ask downgraded to deny: no interactive channel") | ⬜ |
| 27 | Agent edits with `hashedit` a line changed since read; stale edit rejected | `alfred chat` | a real file mutated between read and edit | `alfred` CLI | `hashedit` rejects the stale edit (hash mismatch) rather than corrupting the file; agent re-reads and retries | ⬜ |
| 28 | Agent induced to `web_fetch` an internal address; SSRF guard blocks it | `alfred chat` | `web_fetch http://127.0.0.1:8888` (the key-proxy) | `alfred` CLI | denied by SSRF denylist (internal/metadata addr); key-proxy never reached; log names blocked address | ⬜ |
| 29 | Dev runs a turn with `--output-format stream-json` and reconstructs it offline | `alfred chat --output-format stream-json` | a question triggering a tool call | `alfred` CLI | stdout is valid JSONL (one `{type,payload}` event/line); a minimal replay reconstructs the full turn matching `text`-mode output — symmetric with #12 SSE | ⬜ |

**E2E: none — declared (no user-observable path):**
```
E2E: none — event-bus / 5 registries / ModelProvider-ABC are internal kernel mechanisms; observable only indirectly via rows #1-#17.
E2E: none — "adding a kernel event touches only 2 places" is a code-structure invariant, verified by a regression test (git diff scope), not a user-observable runtime path.
E2E: none — trace store is internal raw-material storage; observable only via what consumes it (#9 distill, #10 evolve, #16 dream).
```

---

## 11. Conflict Review (Phase 6)

- `/work/alfred`: empty repo (LICENSE only) — **no code conflicts, no dead code**.
- No existing docs/specs/plans — **no conflicts**.
- 30 decisions internally aligned (fusion/handoff split left no residual conflict).

---

## 12. Out of Scope / TODO (Decision #30)

Deliberate deferrals (declared, not omitted):

1. **TUI render layer = TODO** — built later via its own mind-explode + wayne-frontend-
   design. BUT the SSE data outlet + e2e #12 replay-criterion ARE in this MVP (zero-rework
   guarantee).
2. **git-worktree multi-agent isolation = TODO ("maybe later")** — filesystem-level
   strengthening of #23a; MVP isolation covers context/tool/state layers; worktree-level
   file isolation deferred.
3. **plugin packaging** — the registry mechanism exists in MVP; formal plugin
   distribution/packaging is later.
4. **Production path (goal C)** — gateways/multi-platform/sandboxing are later.

---

## 13. Open Questions — Per-Module Detail Research (post CEO+Eng review)

The spec above is a coarse blueprint. CEO + Eng reviews rated the architecture sound but
surfaced runtime/concurrency gaps to resolve per-module. Detail research is dispatched
one subagent per module; output in `docs/research/<module>.md`, then back-filled here.

**Scope decision (#31):** keep all 13 subsystems as blueprint (乙); §8 marks Tier-0 first.
**CEO 10th-star:** add a self-measuring eval harness (module #17).

**Eng review findings to resolve (runtime contention on shared state):**
- H1 — iteration budget: atomic decrement under concurrent subagents (single-owner async reservation).
- H2 — async subscriber error policy: isolation + visibility (`subscriber.error` event), not just sync-veto.
- H3 — daemon "session" boundary: when do distill/evolve outputs take effect in a long-lived daemon? (cron fresh-session model + explicit reload trigger for interactive daemon sessions).
- M4 — skill store per-skill write lock (distill/evolve/human + revert); atomic active-`SKILL.md` swap.
- M5 — goal self-continuation: no-progress detector (not just budget cap) + non-convergence e2e row.
- M6 — fusion worker timeout / quorum / judge-failure fallback.
- M7 — corrupt skill = WARNING-skip (catalog error), not crash (config error).
- L8 — autonomy gate must land with/before the first auto-loop (with trace store, before distill).
- L9 — add ~5 negative-path e2e rows (subscriber-raises, budget overshoot, fusion timeout, goal non-convergence, corrupt-skill-skip).
- L10 — REJECTED (Decision #30a): middleware is a confirmed registry (extension surface), not a YAGNI feature; events (observe+veto) ≠ middleware (intercept+transform).

**Per-module research list (17):** kernel:loop+budget · kernel:event-bus · kernel:context+cache · provider-layer · store:session · store:memory · store:trace · store:skill-loader · distill · dream · evolve · goal · fusion · handoff · mcp · control:autonomy+config · eval-harness.

### 13.1 Per-Module Research — Status: COMPLETE

All 17 modules researched; detailed designs (interface sketches, verified field names,
gap resolutions, industry refs) in `docs/research/<module>.md`. wayne-plan should read the
relevant research file when planning each module's units. Key resolutions back-filled:

| Module | Research file | Headline resolution |
|---|---|---|
| loop+budget | kernel-loop-budget.md | H1: await-free `reserve()`, no lock; reserve-before-dispatch |
| event-bus | kernel-event-bus.md | H2: blockable=sequential-veto / background=isolated-gather + `subscriber.error` |
| context+cache | kernel-context-cache.md | H3: "session"=frozen-prefix epoch; epoch-roll at turn_end; verified cache fields |
| provider | provider-layer.md | ABC + own pydantic types; LiteLLM behind boundary; verified usage fields; MockProvider |
| session | store-session.md | SQLite WAL+FTS5; Hermes retry params; session vs trace boundary |
| memory | store-memory.md | dumbest file+RRF retrieval MVP; swappable MemoryProvider; core/facts split |
| trace | store-trace.md | 3-level schema; annotation source-ladder; SQLite+JSONL; replayability |
| skill-loader | store-skill-loader.md | M7: catalog-error skip vs config-error crash; `.versions/` invisibility |
| distill | subsystem-distill.md | 2603.25158 parallel-fleet + prevalence merge; idle/tick; conflict-free |
| dream | subsystem-dream.md | memory-only janitor; dedup/merge/reindex/decay; archive-not-delete |
| evolve | subsystem-evolve.md | 2605.21810 oracle-mutator-selector; M4 per-skill lock + os.replace; DGM upgrade path |
| goal | subsystem-goal.md | Codex 6-status model; M5 SHA256 no-progress + max-continuations |
| fusion | subsystem-fusion.md | composite provider; M6 timeout/quorum/fallback; tool-call decision-voting |
| handoff | subsystem-handoff.md | extension of subagent; extensible payload schema; isolation at construction |
| mcp | mcp.md | mcp SDK client; one dispatch path; anyio FILO teardown; freeze at session_start |
| autonomy+config | control-autonomy-config.md | L8: gate-before-first-loop (constructor-required); ComponentSpec 2-phase validation |
| eval-harness | eval-harness.md | ~120-line consumer; A/B via config; shared `score_rollouts()` with evolve |

**Cross-cutting corrections (Decision #32):** (1) Trace2Skill IDs: 2603.25158=distill,
2605.21810=evolve. (2) eval-harness = consumer (§2 packages), not Ring-3. (3) evolve+eval
share `score_rollouts()`. (4) `${ENV}` interpolation in all config values incl. headers
(no plaintext secrets). (5) LiteLLM: `api_base`/`extra_query`; Azure `wire_api=responses`
— verify at e2e #1.
