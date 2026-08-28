---
name: scar-authoring
description: >
  Author negative-knowledge scars (deadend/fence/landmine) for a repo's .scars/
  directory — qualification criteria, the candidates-only write path, and the
  mandatory-frontmatter / regex-escaping traps that silently break scars.
  Trigger: when you abandon an approach after trying it, when you keep code that
  looks wrong on purpose, or when you discover that changing one thing breaks
  another non-obviously — and you want to record it so the next agent does not
  repeat the pain.
license: MIT
metadata:
  author: Daily-Nerd
  version: "1.0"
---

# Authoring Scars

A scar records *negative knowledge*: a thing that was tried and failed, code
that looks wrong but is intentional, or a non-obvious coupling. Scars fire
automatically — the next editor sees the relevant scar injected before they
touch anchored code. Your job here is to write a good one.

## When to Use

First test, before anything else: *"Would this mistake plausibly recur in a
different context?"* If yes, it qualifies; if it could only ever happen once,
it does not (heuristic from arXiv:2607.13091, a production field report on
accumulated behavioral rules). Then pick the type:

- You **abandoned an approach** after trying it → `deadend`
- You **kept code that looks wrong on purpose** → `fence`
- You **found that changing A breaks B non-obviously** → `landmine`

Not a scar: routine debugging that eventually succeeded. If you tried something,
it worked, and you moved on — there is nothing to record.

## The Three Types

**`deadend` — tried and failed.** Protects against re-attempting a dead path.
Primary anchor is usually `pattern` (the shape of the failed approach).
*Example (scar #2):* an agent tried to install Claude Code hooks by writing
`~/.claude/settings.json` directly; the permission classifier denies it. Anchor
`path: src/scar/installer.py`; body says the *user* must run `scar hook install`.

**`fence` — looks wrong, is intentional.** Protects existing code from a
"cleanup" that would break it. Primary anchor is `path`.
*Example (scar #3):* the installer deliberately ignores an active virtualenv so
hooks bind to a stable `scar` on PATH, not a venv shim that disappears. Anchor
`path: src/scar/installer.py`; body says "do not 'simplify' this to plain
`shutil.which`."

**`landmine` — touching A breaks B.** Anchor the trigger site; the body names
the blast radius. *Example (scar #6):* a regex in a scar's `pattern:` field passes through the
parser verbatim, so a doubled `\\b` stays two characters and matches nothing
real — the anchor silently self-matches only its own `.scars/` body, and the
protection is dead while the gauge reads green.

## The Write Contract (non-negotiable)

1. COPY `.scars/template.md` (or this skill's `assets/template.md`) — do not
   edit the template itself.
2. Write to `.scars/candidates/<slug>.md` with `status: candidate`.
3. **Never** write into `.scars/*.md` directly. A human promotes via
   `scar promote`. (The literature agrees this gate is load-bearing:
   arXiv:2607.13091 warns that "a noisy review culture can poison the rule
   set faster than the validation step can catch" — bad scars amplify, so
   promotion stays human.)
4. If an MCP server is wired, prefer the `scar_draft` tool — it enforces the
   path and runs lint before writing.

## Mandatory Frontmatter

A file without `---`-fenced YAML frontmatter is **not a scar at all** — it never
fires. Minimum valid block: `type`, `title`, `severity`, `confidence`,
`created`, `authors`, at least one `anchors` entry, and `status: candidate`.

## Anti-Over-Escape (the #1 silent failure)

Prefer a `path:` anchor — it cannot self-match and needs no escaping. If you
must use a `pattern:` regex, know that the value reaches the matcher
**verbatim** — this reader is not a YAML parser and nothing un-escapes. Write
the regex exactly as it must execute: `\b` stays `\b`, `\(` stays `\(`.
Doubling backslashes "for YAML" yields a literal backslash and a dead anchor.

The pattern is also matched against all tracked content **including the scar's
own body**, so a broken pattern keeps itself alive by self-reference. Run
`scar lint` and confirm the scar does NOT appear under partial-rot — which
also reports a dead branch inside an alternation, where a live branch would
otherwise mask it.

Wrong: `pattern: "\\bwiden\\b"`  →  Right: `path: src/widen/` (no escaping, no
self-match).

## Anchors, Severity, Size

- `path:` = repo-relative prefix (file or directory). `pattern:` =
  case-insensitive regex over path + new content.
- Severity: `low | medium | high | critical`.
- Injection is capped at ~3 scars / ~700 chars each — write tight: 5–15 lines,
  evidence cited inline.

## Arming a Violation Tripwire (optional, high value)

`violation: "<regex>"` turns a scar from advisory into measurable: after an
edit to anchored code, the regex runs against the added lines — a match means
the forbidden thing was done anyway, and the violation is logged (this feeds
the fired→violated compliance metric).

- **Arm only machine-checkable scars.** If the forbidden act has a concrete
  code shape (`time\.sleep\(`, `except \(KeyError`, a banned API call), write
  it. Prose-level deadends ("don't retry this architecture") stay
  advisory-only — a tripwire that cannot be expressed as a regex honestly
  should not exist.
- **The regex is RAW.** Surrounding quotes are stripped; nothing else
  un-escapes. Write it exactly as it must execute — `\b` stays `\b`, `\(`
  stays `\(`. Doubling backslashes "for YAML" is the over-escape trap that
  kills pattern anchors, and it kills violation regexes the same way.
- **Prove both cases.** Before finishing, run a synthetic
  `scar check --diff` twice: once with a diff that MUST fire the violation,
  once with an innocent diff that must NOT. A tripwire verified only on the
  firing case is untested on the case it will see most.
- The scar's own file is excluded automatically — a scar quoting the
  forbidden construct in its body cannot violate itself. Keep the pattern
  tight anyway: it runs against every anchored edit.

## Verify Before Finishing

- `scar lint` must pass.
- At least one `evidence` receipt (commit / pr / incident / note) — without it
  the scar is challengeable on sight.
- If you armed a `violation:` regex, both `check --diff` cases above proved out.
