# SCAR Format Specification — v0.1

The scar file format, specified independently of any tool. The format is the
long-term bet: tools (this repo's CLI and hooks included) are replaceable
consumers. Stability promise: v0.x may add optional fields; it will not
remove or re-type the fields below.

## 1. Location and naming

```
<repo-root>/.scars/
├── README.md                      # human contract — not a scar
├── template.md                    # copy-me — not a scar (status: template)
├── NNNN-<slug>.<type>.md          # active/archived scars, NNNN zero-padded
└── candidates/
    ├── <slug>.md                  # proposed scars awaiting human review
    └── fp-log.txt                 # self-reported false triggers (free text)
```

Files named `README.md`/`template.md` (case-insensitive) or prefixed `_` are
never parsed as scars. The directory is `.scars` — plural, exactly.

## 2. File format

UTF-8 Markdown with a mandatory YAML frontmatter block (`---` fenced, first
bytes of the file). **A file without frontmatter is not a degraded scar — it
is not a scar at all**, and conforming tools MUST surface it as broken rather
than skip it silently.

The frontmatter is a constrained YAML subset, parseable line-wise (no nested
maps beyond the forms below, no multi-line scalars, no anchors/aliases). This
is deliberate: consumers in hook hot-paths parse with zero dependencies.

### Fields

| Field | Required | Type / values | Notes |
|-------|----------|---------------|-------|
| `type` | yes | `deadend` \| `fence` \| `landmine` | semantics §3 |
| `title` | yes | one line | searchable, states the constraint |
| `id` | active scars | integer, unique per repo | assigned at promotion |
| `severity` | yes | `low` \| `medium` \| `high` \| `critical` | ranking input |
| `confidence` | yes | float 0..1 | decays; raised by surviving challenges |
| `created` | yes | ISO date | |
| `authors` | yes | inline list | agents as `"claude-code"` etc.; reviewer appended at promotion |
| `anchors` | yes, ≥1 | list of `- path:` and/or `- pattern:` | §4 |
| `evidence` | recommended | list of `- commit:`/`- pr:`/`- incident:`/`- note:` | absent ⇒ challengeable on sight |
| `expires.condition` | recommended | quoted string | what change obsoletes this scar |
| `expires.review_after` | recommended | ISO date | forces periodic freshness contact |
| `status` | yes | `candidate` \| `active` \| `challenged` \| `archived` \| `orphaned` \| `template` | lifecycle §5 |
| `receipt_id` | reserved | string ref | **reserved — not yet parsed.** Optional pointer to a cryptographic provenance receipt; see note below. |

`receipt_id` is a forward-compatibility reservation, not a live field. Scar's
trust model is social by design (git history, `authors`, evidence-by-reference);
that is sufficient within one repo or org. It does **not** carry across orgs that
share no git history — the future cross-org / org-graph layer where "this dead end
hit N teams" must be attributable. `receipt_id` reserves the slot for a signed,
content-addressed receipt (e.g. [veritrail](https://github.com/Daily-Nerd/veritrail))
bound to an authorship (`scar_draft`) or promotion event. No tool emits, requires,
or validates it today, and the line-wise parser ignores it like any unknown key —
so existing scars are unaffected. It will not become required in v0.x.

Body: prose after the frontmatter, 5–15 lines. What happened, why, what a
future editor must do instead — written for a reader with zero context.

## 3. Type semantics

- **`deadend`** — an approach was tried and failed. Protects against
  *re-attempt*; primary anchor is usually a `pattern` (the approach
  reappearing in new code anywhere).
- **`fence`** — code looks wrong but is intentional. Protects *existing code
  from change*; primary anchors are paths.
- **`landmine`** — changing A breaks B non-obviously. Anchors the trigger
  site; body names the blast radius.

## 4. Anchors

- `path:` — repo-relative file or directory prefix. Matches any file whose
  repo-relative path starts with it.
- `pattern:` — case-insensitive regex, quoted. Tested against (a) the
  repo-relative path of an edited file and (b) the *new content* being
  written. A content hit is the strongest signal a consumer can receive.

Conforming consumers rank by `anchor_strength × severity × confidence` and
cap injection at **3 scars / ~700 chars body each** — the fatigue budget is a
format-level guarantee, not a tuning knob.

## 5. Lifecycle

```
candidate ──promote (human)──▶ active ──challenge──▶ challenged ─▶ upheld (confidence↑)
    │                            │                        └──────▶ archived
 discard                    anchors rot
                                 ▼
                             orphaned ──re-anchor──▶ active
```

- New scars are ALWAYS born in `candidates/` (humans or agents author; only
  humans promote). Promotion assigns the next free `id`, renames to
  `NNNN-<slug>.<type>.md`, sets `status: active`, appends the reviewer.
- Nothing is ever silently deleted: archived scars keep their file with
  `status: archived`; orphaned anchors are surfaced loudly, never dropped.

## 6. Conformance

A conforming consumer: parses every `.md` under `.scars/` except the skip
set; treats unparseable files as broken and reports them; fires only
`status: active` scars; honors the injection cap; never blocks an edit
(advisory by default — blocking is an explicit, opt-in, per-severity choice
in CI only).

Reference implementation: `src/scar/` in this repository (`scar` CLI:
init/lint/status/promote/check/why/inject/harvest).
