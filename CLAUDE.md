# Alfred — Agent Working Notes

Minimal, frontier-design **agent-loop kernel + pluggable experiment bench**, shipped
**SDK-first** (pure-Python core, zero UI deps) with UI strictly separated. Thesis:
*the harness, not the model, is the dominant variable* — keep the kernel tiny, make every
advanced capability a swappable subsystem you can A/B.

## Source of truth

Design is authoritative in `docs/`, not in this file. Read before non-trivial work:
- **Spec:** `docs/specs/2026-06-15-alfred-design.md` (architecture; §3.5 layered instructions,
  §3.6 tools+permission, §3.7 CLI output, §3.8 store scope; §10 E2E contract).
- **Plan:** `docs/plans/2026-06-15-001-feat-alfred-agent-loop-plan.md` (25 units / 5 milestones,
  each with a Real e2e test + E2E contract rows).
- **Decision logs:** `docs/decisions/*.md` — every design decision with rationale. Five logs:
  hermes-agent-loop (32), layered-instructions (L1-L9), tools-permission (T1-T11),
  cli-json-output (J1-J6), store-scope (S1-S9). **If you change behavior, update the relevant
  log + the spec recap table.**
- **Research:** `docs/research/*.md` (17 module deep-dives — verified interfaces, field names,
  gap resolutions). Pull concrete designs from here; don't re-derive.

## Layout

| Package | Role | UI deps |
|---|---|---|
| `agentkit/` | pure core: `kernel/` (loop, context, budget, events, registries, permission, providers), `stores/` (session, trace, memory, skill, project), `subsystems/` (distill, dream, evolve, goal, fusion, handoff), `mcp/`, `control/` (autonomy, config, proposals), `bundled/skills/`, `eval/` | none |
| `agentkit_cli/` | thin CLI consumer (`alfred` entrypoint) — pure dispatch, zero agent logic | CLI only |
| `agentkit_server/` | optional HTTP/SSE shell + cron daemon (a consumer; the headless daemon) | none |
| `agentkit_eval/` | eval harness (a CONSUMER that starts whole agents to A/B configs — NOT Ring-3) | none |
| `agentkit_fff_linux_x64/` | per-platform companion package bundling the `fff` search binary | none |
| `alfred/` | public import shim (`from alfred import Agent`) | none |

**Three rings (inside `agentkit/`):** Ring-1 kernel (not pluggable) → Ring-2 stores (interface
+ default impl, swappable) → Ring-3 subsystems (event-driven plugins). Ring-3 works ONLY via
hooks/events/registries — never injects logic inward.

## Commands

```bash
uv run pytest                 # unit + integration; live-provider e2e skipped by default
ALFRED_RUN_REAL_E2E=1 uv run pytest tests/e2e  # live LLM contract tests only
uv run pytest tests/integration
uv run ruff check .           # lint (line-length 100, py312, select E/F/I/B/UP)
uv run alfred chat            # run the CLI (--continue, --output-format text|json|stream-json)
uv run python -m ...          # always `uv run python`, never .venv/bin/python
```

## Conventions

- **Python 3.12+, managed with uv.** `uv run python` for everything.
- **loguru** for logging (not stdlib logging); `click` for CLI (not argparse). `-v` → DEBUG.
- **pydantic v2** schema-first: define data shape before behavior. `extra="forbid"` on OWNED
  config (typo crashes at startup); `extra="ignore"` on UNTRUSTED LLM/skill/external input.
- Tests live in `tests/<area>/`; `tests/e2e/` = live LLM contract tests only,
  `tests/integration/` = wired subsystems/mock or direct-store paths, the rest = unit
  (MockProvider, no network).
- Match existing style; surgical changes only. Follow the patterns already in the touched module.

## Engineering invariants (this project leans on them hard)

- **SSoT:** every piece of state has one owner. session/trace/facts → SQLite with a
  `project_id` column (`WHERE project_id=?`); memory `core/` → global files; skill content +
  versions → pure files (`.versions/` + `manifest.json`); goal → JSON file; distill cursors
  and proposal queue → `trace.db`. On-disk active `SKILL.md` is the SSoT for "what loads"
  (loader is file-only).
- **Fail loud, don't degrade silently:** missing config / unreadable declared source / corrupt
  provider call → raise. A *declared* instruction source that can't be read = fail-loud; a
  missing optional one (no AGENTS.md) = DEBUG. Over-cap instructions = WARN (never truncate,
  never block skill/facts loading).
- **Frozen-prefix cache discipline:** system prompt assembled once at session_start and frozen;
  no mid-session mutation; epoch-roll at turn_end. A session = one frozen-prefix epoch.
- **Permission ≠ autonomy (orthogonal axes):** permission = per-tool allow/ask/deny (+ pattern,
  per-agent narrow, strictest-merge); autonomy = off/assist/auto governs auto-loops AND
  interprets the `ask` state. `deny` is a hard wall no autonomy level overrides.
- **Push, don't poll; Delete > Add.**

## Provider / real-LLM env (verified 2026-06-15)

Only `agentkit/kernel/providers/litellm_provider.py` imports `litellm` (regression-guarded).
Real-LLM e2e loads credentials from real local files — never hard-code or invent keys:
- **Anthropic** ← `~/.claude/settings.json` env (`ANTHROPIC_BASE_URL=http://127.0.0.1:8888`,
  `ANTHROPIC_API_KEY`). Model comes from Alfred config / `ALFRED_REAL_MODEL` / exported
  `ANTHROPIC_DEFAULT_*_MODEL`, not Claude Code's top-level `"model"` alias.
- **OpenAI/Azure** ← `~/.codex/config.toml` `[model_providers.custom]` (`base_url=…:8888/openai`,
  **`wire_api="responses"`** — Responses API, not Chat Completions; `api-version=2025-04-01-preview`;
  header `Ocp-Apim-Subscription-Key`; model `gpt-5.5`).
- **Secrets via `env_key`/`${ENV}` indirection only** — never copy a plaintext key into `agent.yaml`.
- **SSRF vs proxy:** `web_fetch` denies `127.0.0.1:8888` (tool egress); the provider layer
  legitimately reaches the same proxy (provider egress). Two paths — the SSRF guard governs the
  tool only.

## Git / commits

- 1 commit = 1 unit/fix. Format: `feat:/alfred - <title>` or `fix:/alfred - <title>` with
  `[why]` / `[how]` body. `git commit -s`, signed off as the human (Jingwen Chen), never a bot.
- **Push uses the `github-wayne` SSH alias** (`git@github-wayne:waynejing995/alfred.git`,
  key `~/.ssh/id_ed25519_waynejing995`). The default `git@github.com:` resolves to an account
  with no write access. If you re-clone, re-point `origin` to the alias.

## Out of scope (declared TODOs)

Sandbox/capability-boundary axis; project relink after move/rename; TUI render layer;
git-worktree multi-agent isolation; formal plugin packaging; Darwin-Gödel evolve archive;
swarm/peer multi-agent. (See spec §12 + decision-log TODO notes.)
