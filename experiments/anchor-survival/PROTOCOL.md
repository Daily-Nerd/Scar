# Experiment: Anchor Survival Replay (Kill Gate 0.2)

**Question.** Do SCAR's symbol + fingerprint anchors survive real-world refactor commits (renames, moves, splits) at ≥80%, where naive path+line anchors die?

**Why it matters.** Anchor rot was ranked #3 fatal risk by the skeptic review. If anchors can't follow code through refactors, scars rot silently and the system decays into the wiki it replaced.

## Subjects (real history, no synthetic refactors)

| Repo | Events | Nature |
|------|--------|--------|
| TripWire | 4 rename commits touching .py | whole-package rename envsync→tripwire (10 files, similarity 54-100%), CLI files moved into security/ subdir (R065/R087), core.py split → _core_legacy.py + new modules (R091), shell script move |
| Heimdall | 1 rename commit touching .tsx | route file rename `devices.$id.tsx` → `devices_.$id.tsx` (R099) |

Events = every commit with `--diff-filter=R` whose renamed files are code (.py/.tsx). Doc-only renames excluded.

## Method

For each refactor commit `C`:

1. **Plant anchors at `C~1`** on every symbol (top-level `def`/`class`, and class methods as qualified `Class.method` pairs) defined in each renamed file's pre-image. Each anchor stores: old path, qualified symbol, normalized content fingerprint of the def block (comments stripped, whitespace collapsed).
2. **Resolve at `C`** with the SCAR resolver v0, in order:
   - a. old path still has the symbol → survived (trivial)
   - b. follow git's own rename record old→new path, search symbol there → survived-via-rename
   - c. repo-wide qualified-symbol search at `C` → unique hit = survived-via-search; multiple hits = attempt **fingerprint disambiguation** (exactly one candidate block matches planted fingerprint → survived-via-fingerprint)
   - d. nothing found → check ground truth: if the symbol name genuinely no longer exists in the post-tree, the anchor **correctly orphans** (excluded from denominator — the code died, not the anchor); if it exists but resolution failed → **FALSE ORPHAN** (failure)
   - e. multiple hits, fingerprint can't disambiguate → **AMBIGUOUS** (failure)
3. **Baseline:** path+line anchor (old path + def line number) resolved naively at `C`. Expected to collapse on renames — quantifies what SCAR's anchor model buys.
4. **Fingerprint drift stat** (secondary, not gated): among survived anchors, how often did the normalized body change (fingerprint mismatch at the new site)? High drift = fingerprints are drift *detectors*, not locators — informs SPEC §2.

## Pre-registered thresholds

- **PASS:** survival = survived / (survived + FALSE ORPHAN + AMBIGUOUS) ≥ 0.80, pooled across all events.
- **KILL:** survival < 0.60 → anchor model needs redesign before any CLI is built.
- 0.60–0.80: inconclusive → analyze failure classes, revise resolver, rerun once.
- Baseline reported alongside for contrast; no threshold (it's the strawman).

## Limitations (declared)

1. Regex symbol extraction (indent-tracked), not tree-sitter — prototype underestimates the production design.
2. Python-dominant sample (one .tsx event). Go/Rust/TS coverage untested.
3. Method-name ambiguity (`__init__`, `validate`) is the expected hard case — that's deliberate; it's where fingerprint disambiguation earns its place.
4. Single-commit horizon: measures survival across one refactor, not compounded drift over months.
