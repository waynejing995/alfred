# Ring-2 Store — Skill Loader (multi-root, L0/L1/L2 progressive disclosure)

Date: 2026-06-15
Module: `agentkit/stores/skill/` (Ring-2, interface + default impl)
Spec refs: §4.3, §6.3, §7; Decisions #12, #14, #20a, #20a-i, #20b, #24; Eng review M4, M7, L8/L9; e2e #4, #5, #9, #10.

---

## Module scope

The skill loader is the **SSoT for the set of skills visible to a session**. It scans the
ordered `skill_sources` roots once at `session_start`, parses each skill's frontmatter,
resolves same-name conflicts by precedence, applies `skill_filter`, and produces a
**frozen L0 catalog** (name + description) that the kernel injects into the static system
prefix. It then serves L1 (full `SKILL.md` body) and L2 (referenced files) on demand via
tools.

In scope:
- Ordered multi-root scan; precedence = root order (`./skills` > `~/.alfred/skills` > `bundled`).
- Frontmatter parse + validation; identity = frontmatter `name`.
- Same-name conflict resolution + fail-loud WARNING naming shadowed skills (e2e #5).
- L0 index build (frozen at `session_start`); L1/L2 on-demand read tools.
- `skill_filter` (include/exclude tags + `disable:[...]` blacklist).
- `.versions/` archive exclusion (loader-invisible; ties to evolve revert, #20a-i).
- **Catalog error handling**: one corrupt skill = WARNING-skip, NOT crash (M7).

Explicitly OUT of scope (boundaries enforced elsewhere):
- **Writing / mutating skills** — distill (new skill), evolve (variant + version), and
  `revert` write skills; the loader only reads. The per-skill write lock + atomic
  `SKILL.md` swap (M4) lives in the **skill *store* write path**, a sibling concern; the
  loader just reads the active `SKILL.md` and never sees in-flight writes (it's frozen).
- **Hot-reload** — list frozen at `session_start`; no inotify (Decision #12, cache economics).
  Daemon reload semantics are H3's problem (cron = fresh session; interactive daemon =
  explicit reload trigger), not the loader's.
- **Skill triggering / selection** — the model decides when to use a skill from the L0
  description; the loader does not match or rank.

The default impl mirrors **Hermes `agent/skill_utils.py`** (the spec's stated reference):
`parse_frontmatter()` + a system-prompt builder that emits a compact L0 index, plus
`skills_list()` / `skill_view(name[, path])` read tools. Format = the **agentskills.io /
Anthropic Agent Skills** open standard, so wayne-* private skills copy into `bundled` with
zero changes (Decision #24).

---

## SKILL.md format

### Frontmatter schema (compatible with Claude Code / agentskills.io)

The Claude Code skill validator (`skill-creator/scripts/quick_validate.py`) and the
agentskills.io spec agree exactly. **Alfred adopts this schema verbatim** so the format is
a strict superset-compatible match (zero-friction wayne-* ingestion, Decision #24).

| Field | Required | Constraint |
|---|---|---|
| `name` | yes | 1–64 chars; `^[a-z0-9-]+$`; no leading/trailing `-`; no `--`; **must match parent dir name** |
| `description` | yes | 1–1024 chars; non-empty; no angle brackets (`<` `>`); "what it does AND when to use it" |
| `license` | no | short string (license name or bundled file ref) |
| `compatibility` | no | ≤500 chars; environment requirements (rarely needed) |
| `metadata` | no | arbitrary string→string map (e.g. `author`, `version`) |
| `allowed-tools` | no | space-separated tool allowlist, e.g. `Bash(git:*) Read` (experimental) |

**Allowed-set is closed for validation but tolerant on read.** The canonical Claude Code
validator rejects unknown top-level keys; the agentskills.io spec says "spec-compliant
runtimes ignore frontmatter keys they do not recognize." Reconcile per the boundary
contract (`extra="ignore"` at external boundaries, KB `llm-prompt-and-boundary-contracts`):

- **Pydantic model `extra="ignore"`** — unknown frontmatter keys are dropped, not fatal.
  Skills are *external/untrusted* input (a dropped-in folder), not owned config; crashing
  on an unknown key would brick startup over a forward-compat field. This is the deliberate
  inverse of `AgentConfig`'s `extra="forbid"` (Decision #13) — owned config is strict,
  third-party skill files are lenient.
- Validate only the fields Alfred *uses*: `name`, `description` (required, hard rules
  above), and the optional ones it reads.

### Alfred-specific extension fields (under `metadata`, not new top-level keys)

To stay format-compatible we do **not** add top-level keys. Alfred's own needs ride inside
`metadata.*` (an explicitly arbitrary map):

```yaml
metadata:
  origin: human          # human | distill | evolve | revert  (for #20a write-protection policy)
  tags: "pdf, finance"   # space/comma list consumed by skill_filter (see below)
```

- `origin` — feeds the evolve write-permission policy (#20a: human-authored protected by
  default; distill/evolve output freely mutable). Default when absent = `human` (fail-safe:
  unknown provenance is treated as protected).
- `tags` — drives `skill_filter` include/exclude (Decision #14). Lives in `metadata` because
  the spec has no top-level `tags`; keeping it there preserves Claude Code compatibility.
- Version lineage, source traces, pass-rate, lesson-bank refs, and the active pointer live in
  `.versions/manifest.json`, not in SKILL.md frontmatter. The active file on disk is the
  loader SSoT.

> Hermes uses extra top-level keys (`platforms`, `required_environment_variables`,
> `setup.collect_secrets`, `metadata.hermes.config`). We deliberately do NOT copy
> `platforms` as a top-level key — `extra="ignore"` means a wayne-*/Hermes skill carrying
> it still loads fine, we just don't act on it in MVP. (Platform filtering is a possible
> post-MVP `skill_filter` extension — see Open questions.)

### Directory structure

```
<root>/
└── <skill-name>/                 # dir name == frontmatter name (enforced)
    ├── SKILL.md                  # required: frontmatter + body (L1)
    ├── references/               # optional: docs read on demand (L2)
    ├── scripts/                  # optional: executable code (L2; runnable w/o full load)
    ├── assets/  (a.k.a templates/)  # optional: templates/static resources (L2)
    ├── evals/                    # optional: eval set (NOT loaded; packaging excludes it)
    └── .versions/                # INVISIBLE to loader (dot-prefixed; see below)
```

- `references/` `scripts/` `assets/` are the three canonical Anthropic dirs; agentskills.io
  lists the same three. Hermes additionally references `templates/`. **Accept both
  `assets/` and `templates/`** as L2 source dirs (alias) for wayne-*/Hermes compatibility.
- File references in `SKILL.md` are **relative paths from skill root**, one level deep
  (e.g. `references/REFERENCE.md`). L2 reads are path-validated against the skill root.

---

## Multi-root loader

### Scan + precedence (Decision #12)

```python
def build_catalog(roots: list[Path], filt: SkillFilter) -> Catalog:
    seen: dict[str, LoadedSkill] = {}          # name -> winner
    shadowed: list[tuple[str, Path, Path]] = []  # (name, loser_root, winner_root)
    errors: list[tuple[Path, str]] = []          # catalog errors (M7)

    for root in roots:                          # roots in PRECEDENCE order, high->low
        if not root.exists():
            logger.debug("skill root absent, skipping: {}", root)   # not an error
            continue
        for skill_dir in iter_skill_dirs(root): # globs OUT dotfiles (.versions/ etc.)
            try:
                sk = load_skill(skill_dir)       # parse + validate frontmatter
            except SkillCatalogError as e:       # corrupt skill = catalog error
                logger.warning("skipping corrupt skill {}: {}", skill_dir, e)
                errors.append((skill_dir, str(e)))
                continue                         # SKIP, do NOT crash (M7)
            if sk.name in seen:                  # same-name conflict
                shadowed.append((sk.name, skill_dir, seen[sk.name].dir))
                continue                         # first (higher root) already won
            seen[sk.name] = sk

    for name, loser, winner in shadowed:         # fail-loud, names the shadowed (#12, e2e #5)
        logger.warning(
            "skill '{}' in {} shadowed by higher-precedence {}", name, loser, winner)

    catalog = apply_filter(seen, filt)           # disable > tag > all (Decision #14)
    return Catalog(skills=catalog, errors=errors, shadowed=shadowed)
```

Key points:
- **Precedence = first-wins down the ordered root list.** Because roots are visited
  high→low, the first occurrence of a name is the winner; later same-name entries are
  recorded as shadowed and skipped. Identity is the frontmatter `name`, never the path
  (SSoT, Decision #12).
- **Conflict detection only spans *active* skills** — `.versions/` archives are globbed
  out before this loop, so v1/v2/v3 of one skill never trip the same-name WARNING (#20a-i).
- **Missing root ≠ error.** `./skills` not existing (the common case) is DEBUG, not WARNING
  — it's a normal "trigger condition not met", and mislabeling it is signal-to-noise (§7).
- **`iter_skill_dirs` excludes dotfiles**: `[d for d in root.iterdir() if d.is_dir() and not d.name.startswith('.')]`.
  A skill dir is one that contains a `SKILL.md`; a dir without one is silently ignored
  (it's not a skill, e.g. a stray `_shared/`).

### L0 / L1 / L2 progressive disclosure (Hermes model)

Hermes's exact API is the reference (`agent/skill_utils.py` + system-prompt builder):

| Level | What | Mechanism | When |
|---|---|---|---|
| **L0** | `name` + `description` (+ optional `tags`/category) of every catalog skill | built into the **frozen static system prefix** at `session_start` | always in prompt |
| **L1** | full `SKILL.md` body | `skill_view(name)` tool | on demand, when model decides a skill fits |
| **L2** | a referenced file under `references/`/`scripts/`/`assets/` | `skill_view(name, path)` tool | on demand, while executing a skill |

- **L0 is cheap by design** — name+description is ~100 tokens/skill (Anthropic) / ~3k total
  for the whole index (Hermes). Decision #14: all skills enter L0 by default; "optimizing
  L0 size is YAGNI" for the dozens-of-skills regime. The L0 block is part of the cached
  static prefix (Decision #21/#29) → it must NOT change mid-session (hence the freeze).
- **Escalation is model-driven via tools**, not loader-driven. Alfred registers two
  read-only tools into the `tools` registry (mirrors Hermes `skills_list` / `skill_view`):
  - `skills_list()` → returns the L0 catalog (redundant with the in-prompt index but lets
    the model re-query; cheap). Respects the frozen catalog.
  - `skill_view(name, path=None)` → `path=None` returns L1 body; `path="references/x.md"`
    returns that L2 file (path-validated to skill root, dotfiles refused).
- **`skill_used` event (Decision #7) fires on `skill_view(name)` (L1 load)** — that is the
  observable signal that a skill was actually adopted; evolve subscribes to it (#18a, S5).
  L0-in-prompt is *availability*, L1-view is *use*. (e2e #4 pass = skill visibly loaded at
  L0 and adopted → the adoption signal is the L1 view.)
- L2 `scripts/` files can be **executed without being loaded into context** (run via the
  Bash/exec tool by path) — the loader only needs to expose the path, per Anthropic's
  "scripts can execute without loading" note.

---

## Gap answers (M7) — corrupt skill = WARNING-skip, not crash

**Eng review M7:** one bad frontmatter in `./skills` must not brick startup. The §7
"corrupt skill → raise" line is too coarse; it conflates two error classes. Resolve by
**distinguishing catalog errors from config errors**:

| Class | Source | Policy | Rationale |
|---|---|---|---|
| **Config error** | owned `AgentConfig` (`agent.yaml`): bad `skill_sources`, malformed `skill_filter`, unknown config key | **CRASH at startup** (`extra="forbid"`, Decision #13) | owned config typo = operator mistake, fail at the door |
| **Catalog error** | a *third-party skill folder*: unparseable YAML, missing `name`/`description`, name≠dirname, name fails kebab rule, duplicate-after-filter, unreadable `SKILL.md` | **WARNING + SKIP that one skill**, continue building catalog | one dropped-in bad skill must not deny-of-service the whole agent |

This mirrors the existing §7 precedent for evolve candidates (#20b: missing-trace = filter
condition = silent skip, fail-loud reserved for *true* exceptions). A corrupt skill in a
multi-author roots scan is the catalog analogue: it's a per-item data problem, not a
loop-breaking exception.

### Concrete error taxonomy + handling

```python
class SkillCatalogError(Exception): ...          # per-skill, recoverable by skipping

def load_skill(skill_dir: Path) -> LoadedSkill:
    md = skill_dir / "SKILL.md"
    if not md.exists():
        raise SkillCatalogError("no SKILL.md")            # not a skill dir -> actually: skip silently upstream
    try:
        raw = md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise SkillCatalogError(f"unreadable: {e}")
    fm = split_frontmatter(raw)                            # must start with '---\n...\n---'
    if fm is None:
        raise SkillCatalogError("missing/!malformed frontmatter fence")
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError as e:
        raise SkillCatalogError(f"bad YAML: {e}")
    if not isinstance(data, dict):
        raise SkillCatalogError("frontmatter is not a mapping")
    try:
        meta = SkillFrontmatter.model_validate(data)       # pydantic, extra='ignore'
    except ValidationError as e:                           # missing name/desc, bad kebab, >limits
        raise SkillCatalogError(f"invalid frontmatter: {e}")
    if meta.name != skill_dir.name:
        raise SkillCatalogError(f"name '{meta.name}' != dir '{skill_dir.name}'")
    return LoadedSkill(name=meta.name, dir=skill_dir, meta=meta)
```

- **Every `load_skill` failure raises `SkillCatalogError`**, caught in the scan loop →
  `logger.warning(...)` + skip + recorded in `Catalog.errors`. Startup proceeds with the
  remaining N−1 skills.
- **The skip is visible, not silent** — WARNING level (not DEBUG), so the operator sees
  "skill X skipped: bad YAML at line 4". `Catalog.errors` is also surfaced (e.g. a startup
  banner `2 skills skipped — run \`alfred skills doctor\``). Honors Fail-Loud's *visibility*
  requirement without the *crash* — degradation is loud, not silent.
- **What still crashes (genuine config errors):** a `skill_sources` entry that is a *file*
  not a dir, a `skill_filter` with a non-list `disable`, an unknown key in `agent.yaml`.
  These come through `AgentConfig` validation (`extra="forbid"`) and crash before the scan.
- **e2e (L9 negative-path row, recommended):** drop a skill with broken YAML into `./skills`
  alongside a good one → startup logs WARNING naming the bad skill, the good skill still
  loads and is usable, exit code 0. (Pairs with e2e #4/#5.)

> Note on **M4 (write-time atomicity)** vs M7 (read-time robustness): they meet at the
> *active `SKILL.md` swap*. The write path (distill/evolve/revert) must swap `SKILL.md`
> atomically (write temp + `os.replace`) under a per-skill lock, so the loader — which
> reads `SKILL.md` whole at `session_start` — never observes a half-written file. Because
> the loader is frozen-at-start and reads the file in one `read_text`, a torn read is
> impossible *given* atomic replace. The loader therefore needs no lock of its own; it
> relies on the writer's `os.replace` atomicity. (Full M4 design is the skill-store-write
> module; flagged here as the contract the loader depends on.)

---

## Versions invisibility (`.versions/` archive)

Ties to evolve's safety = versioning + revert (Decision #20a) and the hard constraint that
**history must not affect loading** (Decision #20a-i).

### Layout

```
<root>/<skill>/
├── SKILL.md            # the ACTIVE version — the only thing the loader sees
├── references/ ...
└── .versions/          # dot-prefixed -> globbed out of the scan
    ├── v1/             # cold whole-dir backup; one dir per archived version
    │   ├── SKILL.md
    │   ├── references/
    │   ├── scripts/
    │   └── assets/
    ├── v2/
    └── manifest.json   # {active: "v3", history: [{ver, ts, origin, parent, ...}], ...}
```

- **Invisibility mechanism = single rule**: `iter_skill_dirs` skips any dir whose name
  starts with `.`. `.versions/` is therefore never descended into, so:
  - archived versions never enter the L0 index,
  - they never participate in same-name conflict detection (#20a-i's exact requirement —
    a naive `versions/` would make v1/v2/v3 all scan as same-name skills and trip the
    shadow WARNING). One dot-prefix kills both.
- **SSoT: a skill's identity = its active directory.** The active version is the top-level
  skill directory (`SKILL.md` plus `references/` / `scripts/` / `assets/`). `.versions/`
  is *revert-only cold backup* — not a loadable entity. (We do NOT make the loader resolve
  frontmatter and read from `.versions/`; that would put two skills' worth of read logic in
  the hot path and re-introduce ambiguity. Active = the top-level skill dir, full stop.)
- **Revert (e2e #10) = a write-path op, not a loader op**: `revert` copies the whole
  `.versions/v2/` directory back over the active skill dir (atomic directory-safe swap via
  the writer) and updates `manifest.json.active`. Next `session_start`, the loader picks up
  the restored active directory with zero version-awareness. "Old version still loadable
  after revert" (e2e #10 observable) is satisfied because revert makes the old version
  *the active directory*.
- **Why dot-prefix over a sibling `skill_archive/` root**: keeping the archive *inside* the
  skill dir keeps a skill self-contained (copy/move/delete the dir = move its whole
  history), and avoids a second scan-exclusion rule. `.versions/` is also excluded by the
  packaging `.skill` zip (extend the existing `ROOT_EXCLUDE_DIRS = {"evals"}` →
  add `.versions`, plus `EXCLUDE_DIRS` already drops `__pycache__`).

---

## skill_filter (Decision #14)

Optional, for experiment variable-control. **Precedence: `disable` blacklist > tag filter
> default all-load.** Default (no filter) = every catalog skill enters L0.

### Schema (lives in `AgentConfig`, `extra="forbid"` — it's owned config)

```yaml
skill_filter:                 # entire block optional; absent = load all
  include_tags: []            # if non-empty: keep only skills having >=1 of these tags
  exclude_tags: []            # drop skills having any of these tags
  disable: []                 # exact skill names to drop unconditionally (blacklist)
```

```python
class SkillFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_tags: list[str] = []
    exclude_tags: list[str] = []
    disable: list[str] = []

def apply_filter(skills: dict[str, LoadedSkill], f: SkillFilter) -> dict[str, LoadedSkill]:
    out = {}
    for name, sk in skills.items():
        if name in f.disable:                          # 1. disable wins absolutely
            logger.info("skill '{}' disabled by skill_filter.disable", name)
            continue
        tags = parse_tags(sk.meta.metadata.get("tags", ""))   # space/comma split -> set
        if f.include_tags and not (tags & set(f.include_tags)):
            logger.debug("skill '{}' excluded: no include_tag match", name)
            continue
        if tags & set(f.exclude_tags):                 # 2. tag filter
            logger.debug("skill '{}' excluded by exclude_tags", name)
            continue
        out[name] = sk                                  # 3. default keep
    return out
```

- **Precedence is enforced by evaluation order**: `disable` checked first (unconditional
  drop), then `include_tags` (allowlist-if-present), then `exclude_tags`. A skill in
  `disable` is dropped even if its tags match `include_tags` (Decision #14's exact rule:
  "kill this one bad skill without editing it" — Delete>Add).
- **`disable` is by `name`**, the SSoT identity — same key the conflict resolver uses.
- **Filter runs *after* conflict resolution**, on winners only. So `disable: [foo]` drops
  the winning `foo` regardless of which root it came from (you don't accidentally "promote"
  a shadowed lower-precedence `foo`). This matches intent: disable removes the skill, it
  doesn't reveal the shadowed one.
- **`disable` drop is INFO, tag drops are DEBUG.** Disabling a named skill is an explicit
  operator intent worth seeing at default verbosity; bulk tag filtering is routine
  experiment-config noise → DEBUG.
- **Filter changes take effect next session** (frozen at `session_start`, like the catalog
  itself) — consistent with no-hot-reload (Decision #12).

---

## Industry refs (URLs)

- Anthropic — Equipping agents for the real world with Agent Skills (3-tier disclosure,
  ~100-tok metadata, <5k body): https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Claude API docs — Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- agentskills.io — Specification (canonical frontmatter: name≤64 kebab, desc≤1024,
  license/compatibility≤500/metadata/allowed-tools; name must match dir; references/scripts/assets):
  https://agentskills.io/specification
- anthropics/skills — public Agent Skills repo (skill-creator, validator, schemas):
  https://github.com/anthropics/skills
- SKILL.md Format Specification (DeepWiki, anthropics/skills): https://deepwiki.com/anthropics/skills/2.2-skill.md-format-specification
- Hermes Agent — Skills System (L0 `skills_list()` ~3k tok, L1 `skill_view(name)`,
  L2 `skill_view(name, path)`, `~/.hermes/skills/`, no restart): https://hermes-agent.nousresearch.com/docs/user-guide/features/skills/
- Hermes skills system internals (DeepWiki — `parse_frontmatter()` in `agent/skill_utils.py`,
  L0 index builder, platform filtering): https://deepwiki.com/NousResearch/hermes-agent/8-skills-system
- On-disk canonical reference (this machine): Claude Code `skill-creator` validator
  `quick_validate.py` (`ALLOWED_PROPERTIES = {name, description, license, allowed-tools,
  metadata, compatibility}`; kebab ≤64; desc ≤1024 no angle brackets) +
  `package_skill.py` (`.skill` zip, `ROOT_EXCLUDE_DIRS={"evals"}`, excludes `__pycache__`).

---

## Open questions

1. **L0 description budget cap?** Decision #14 says all-skills-in-L0 is fine for dozens.
   With wayne-* + bootstrap + distilled skills the `bundled` root could grow to many dozens;
   at what count does L0 (~100 tok each) start eating the cache prefix meaningfully? Cheap
   guard: a startup WARNING if the L0 block exceeds e.g. 8k tokens, suggesting `skill_filter`.
   Not blocking MVP.
2. **`platforms` filtering as a `skill_filter` extension?** Hermes filters skills by OS
   (`platforms: [linux, macos]`). MVP `extra="ignore"` loads such skills regardless. If
   wayne-* skills carry OS-specific scripts, a post-MVP `skill_filter.platform` (auto from
   host) would prevent loading unusable skills. Deferred; flag only.
3. **`allowed-tools` invocation scope.** Resolved after tools-permission T10 and the plan
   alignment audit: Alfred enforces this field, but only during an explicit skill
   invocation context. `skill_view(name)` loads text and emits `skill_used`; it does not
   permanently narrow the whole session. A skill-run/worker pushes `skill_name` into
   `ToolCallContext`, the permission resolver adds `permission_layer_for_skill`, and the
   scope is popped when that invocation completes. Multiple active scopes resolve by the
   strictest permission; viewing L1 alone never broadens or narrows unrelated tool calls.
4. **name≠dirname strictness.** agentskills.io *requires* name==dirname; some on-disk
   wayne-* skills may not comply. M7 treats mismatch as a catalog error (skip+WARNING). Is
   skip too harsh for the copy-in case, or is "fix the dir name" the right forcing function?
   Recommend keep strict (it's the identity SSoT; a one-time rename at copy is cheap) but
   note it as a wayne-* ingestion checklist item for Decision #24.
5. **Daemon catalog refresh (H3 overlap).** Loader is frozen per session; in a long-lived
   interactive daemon, when distill/evolve write a new/changed skill, the operator needs an
   explicit reload to pick it up. The *reload trigger* is H3's design; the loader just needs
   to expose a `rebuild_catalog()` that the daemon can call to start a fresh frozen snapshot.
   Confirm the trigger contract with the daemon-session module.
