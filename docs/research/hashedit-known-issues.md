# `hashedit` / `hashread` — Known Issues (port fidelity vs. upstream)

> **Status (2026-06-15): Issues 1–3 FIXED.** `file_hash.py` now uses the upstream
> normalize→operate→restore seam (`_read_normalized` / `_serialize`, `newline=""` on read and
> write). CRLF, BOM, and no-trailing-newline files round-trip byte-for-byte; covered by
> `tests/tools/test_hashedit.py`. Issue 4 (32-bit anchor) is by design and now carries a
> threat-model comment. This doc is retained as the rationale record.

Date: 2026-06-15
Module: `agentkit/tools/file_hash.py` (Ring-1 builtin tools)
Origin of idea: Can Bölük's `oh-my-pi` hash-anchored edits (`packages/hashline/`).
Upstream refs: `normalize.ts` (`detectLineEnding` / `normalizeToLF` / `restoreLineEndings`,
`stripBom`), `fs.ts` ("the patcher does its own BOM stripping and LF normalization between
`readText` and `writeText`"), `snapshots.ts` (`Snapshot.text` = "Full normalized (LF, no BOM)
file text").

> **Why this matters.** The whole contract of a hash-anchored editor is *surgical, verified,
> byte-faithful* single-line edits — "apply only if the line the model saw is still the line
> on disk." Our current port silently violates byte-faithfulness on two common file shapes
> (CRLF, BOM). That turns a safety tool into a corruption tool: the edit "succeeds," the
> anchor "matches," and the file is quietly rewritten in a different encoding. This is exactly
> the *Fail-Loud / don't-degrade-silently* failure mode — there is no error, just drift.

---

## The correct design (from upstream)

Hash-anchored editing is a **normalize → operate → restore** sandwich:

1. Read **raw** text (no newline translation).
2. `stripBom` — capture a leading UTF-8 BOM, remove it, remember it.
3. `detectLineEnding` — record the file's ending (`\r\n` vs `\n`; first occurrence wins).
4. `normalizeToLF` — convert all endings to `\n`. **Hashing and editing happen on this
   LF-normalized, BOM-stripped text.** The newline is *never* part of the hashed content —
   only the line's text is.
5. Apply edits on the normalized text.
6. `restoreLineEndings` + re-prepend BOM on write-back.

Invariant: **hash/edit in canonical LF form; capture the original shape (BOM + ending) and
restore it on write.** A file that round-trips with no logical change must be byte-identical.

Our port agrees on the one decision that matters most — the hash covers line content only,
not the newline (`line_hash(content)`, `file_hash.py`). It diverges on the normalize/restore
seam.

---

## Issue 1 — CRLF line endings silently converted to LF (data corruption)

**Severity: high (silent data loss).**

`_read_line_records` carefully detects `\r\n`, `\r`, and `\n` per line and stashes the ending
in `_LineRecord.ending`. But `Path.read_text()` performs **universal-newline translation**:
by the time `splitlines(keepends=True)` runs, every ending is already `\n`. The `\r\n` / `\r`
branches are **unreachable dead code** — `_LineRecord.ending` is always `\n` (or `""` for a
last line without a trailing newline).

On write-back, `record.content + record.ending` therefore emits LF for every line, including
lines the model never touched.

Reproduction (verified):

```
input file bytes:  b'alpha\r\nbeta\r\n'
hashedit line 2 → 'bravo'
output file bytes: b'alpha\nbravo\n'   # every CRLF silently became LF
```

Editing line 2 of a Windows-format file rewrites line 1's ending too. The diff the tool
returns shows only the one intended line; the encoding change is invisible.

## Issue 2 — UTF-8 BOM folded into line 1's content and hash

**Severity: medium (corrupts hash anchor + content).**

There is no BOM handling. `Path.read_text()` keeps a leading `﻿`, so it becomes the
first character of line 1's content — which means it is **hashed** as content and **echoed**
in `hashread` output.

Reproduction (verified):

```
input file bytes:  b'\xef\xbb\xbfalpha\nbeta\n'
hashread row 0:    '1:9ecdd28f|﻿alpha'   # BOM is inside the content + baked into the hash
```

Consequences: (a) the model sees a stray BOM char in line 1; (b) the anchor hash is computed
over BOM+text, so it won't match a hash computed by any BOM-aware producer; (c) editing line
1 and writing back may drop or duplicate the BOM depending on whether the model echoed it.

## Issue 3 — last line without trailing newline (minor, related)

`splitlines(keepends=True)` on `"a\nb"` yields `["a\n", "b"]`; the final record gets
`ending=""`, which is correct *today only by luck* because Issue 1 already flattens everything
to LF. Once the normalize/restore seam is fixed (Issues 1–2), trailing-newline presence must
be preserved explicitly. Upstream tracks this via the trailing "phantom line" sentinel
(`apply.ts` `trailingPhantomLine`).

## Issue 4 — 4-byte (32-bit) anchor hash (by design — document the threat model)

`HASH_SIZE = 4` → 32-bit digest. This is an **inherited upstream choice** (short anchors cost
fewer tokens when the model echoes them back), not a defect. It is adequate for the actual
threat model — "did the model read a stale version of this line" — but it is **not**
collision-resistant against adversarial input (~1-in-4-billion per line). Keep it; add a
one-line comment stating the threat model so it isn't "hardened" later by mistake.

---

## What's already correct (don't regress these)

- Two-phase **validate-then-apply**: every anchor is checked before any write → no partial
  writes (`file_hash.py`).
- Duplicate-line and out-of-range rejection.
- Newline-in-content rejection enforces the one-line-per-edit contract.
- Hash over **content only**, never the ending — matches upstream's core decision.
- `frozen=True` dataclasses; `_coerce_edit` accepting both `dict` and `HashEdit`.

---

## Recommended fix

Mirror upstream's seam. Replace the per-line `_LineRecord.ending` scheme (a granularity
upstream doesn't even have — YAGNI) with a file-level normalize/restore:

```python
def _read_normalized(path: Path) -> tuple[list[str], str, str, bool]:
    raw = path.read_text(newline="")                  # NO universal-newline translation
    bom = "﻿" if raw.startswith("﻿") else ""
    if bom:
        raw = raw[1:]
    ending = "\r\n" if "\r\n" in raw else "\n"         # detectLineEnding
    lf = raw.replace("\r\n", "\n").replace("\r", "\n")  # normalizeToLF
    had_final_newline = lf.endswith("\n")
    lines = lf.split("\n")
    if had_final_newline:
        lines.pop()                                    # drop split-induced trailing ""
    return lines, ending, bom, had_final_newline
```

Hash and edit on `lines` (pure LF content). On write-back:

```python
body = "\n".join(lines) + ("\n" if had_final_newline else "")
path.write_text(bom + body.replace("\n", ending), newline="")  # restoreLineEndings, no re-translation
```

`newline=""` on **both** read and write is mandatory — it disables Python's universal-newline
translation in both directions (without it, `write_text` re-translates `\n` → `os.linesep`).

### Test coverage to add (`tests/tools/test_hashedit.py`)

The bugs slipped through because every fixture uses LF, no-BOM text. Add round-trip tests:

1. **CRLF round-trip** — edit line 2 of `b'alpha\r\nbeta\r\n'`; assert output bytes are
   `b'alpha\r\nbravo\r\n'` (untouched line keeps CRLF).
2. **BOM round-trip** — `hashread` of a BOM file must not include `﻿` in content; edit a
   line; assert the BOM survives at byte 0 and is not duplicated.
3. **No-trailing-newline** — edit the last line of `b'a\nb'`; assert output is `b'a\nB'`
   (no newline added).
4. **Mixed / lone-CR** — optional; upstream normalizes lone `\r` to the detected ending.

---

## Open questions

1. **Mixed-ending files.** Upstream picks one ending for the whole file (first occurrence) and
   rewrites all lines to it on save. That means a mixed-ending file gets normalized on first
   edit — acceptable, but it *is* a (logical-no-op) byte change to untouched lines. Confirm
   we accept upstream's behavior rather than the strictly-faithful per-line approach (which
   nobody asked for).
2. **Non-UTF-8 encodings.** `read_text()` assumes UTF-8. Latin-1 / UTF-16 files will raise or
   mojibake. Upstream is also UTF-8-centric (Bun `file.text()`); document as out-of-scope or
   fail loud on decode error rather than guessing.
3. **Should `hashedit` reject a file whose BOM/ending it can't round-trip?** Fail-loud
   alternative to silent normalization for the mixed-ending case.
