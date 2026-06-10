# Anchor Survival Replay — Results (Kill Gate 0.2)

Run: 2026-06-09. Real refactor history, no synthetic events. Protocol pre-registered (commit before run). Gate: ≥80% survival.

## Verdict: ✅ PASS — both horizons

| Test | Events | Survival | Baseline (path+line) |
|------|--------|----------|----------------------|
| **Single-commit replay** (TripWire 4 rename commits + Heimdall 1): package rename, subdir moves, file split | 197 anchors planted, 193 gated | **183/193 = 94.8%** | **0/197 = 0.0%** |
| **Long-horizon stress** (plant at envsync era → resolve at HEAD: ~200 commits later, TWO package renames, file split, CLI restructure, zero re-anchoring in between) | 137 anchors, 133 gated | **117/133 = 88.0%** | n/a (old paths all dead) |

The baseline number is the headline: **every single path+line anchor died** on the same events the SCAR resolver survived at 95%. Line-number anchors are not a simplification — they are zero.

## Resolution-path breakdown

- Single-commit: 183/183 survived via git rename-map (step b). Git's own content-similarity rename detection carries everything across one commit — cheap and reliable.
- Long-horizon: all 117 survivals came via **repo-wide qualified-symbol search** (step c) — the rename chain was too long/too rewritten for naive following, but the symbols were findable. This is the path that earns the design.

## Failure anatomy (16 total, all explained)

1. **10× FALSE-ORPHAN — all one event:** `class EnvSync` was renamed to `class TripWire` *inside* the refactor commit. A symbol rename (not a file rename) changes the anchor's identity; name-based resolution cannot follow it, by construction. Counted as failures per the pre-registered metric, but the *designed* behavior is exactly what happens: the scar orphans **loudly** and enters the re-anchor queue — where the fix is mechanical (the new class has a near-identical method set; an agent or fuzzy fingerprint match re-anchors it in seconds).
2. **6× AMBIGUOUS — generic names:** `main`, `scan`, `audit`, `validate`, `validate_config`, `validator` each have multiple definition sites at HEAD, and 200 commits of body rewrites defeated exact-fingerprint disambiguation. Mitigations for v1: fuzzy fingerprint (token-set similarity, not exact hash), module-qualified anchors (`cli.main` not `main`), and import-graph hints.

## Secondary findings

- **Fingerprint drift: 21.3%** (39/183) across even a *single* rename commit. Exact-hash fingerprints break too often to locate code — but that's fine: SPEC §2 assigns them drift *detection*, and this number confirms the assignment. v1 should use similarity, not equality, for any locating role.
- **Tooling gotcha (cost 2 debug cycles, worth recording):** `git grep -E` treats `\b` as a literal (POSIX ERE has no word boundary) and silently matches nothing; and a pathspec glob that matches zero files (`*.tsx` in a pure-Python repo) silently empties the whole result. Both produced a fake "0% survival" run before the fix. Any production `scar` resolver must integration-test its git layer per platform.
- The 4 orphan-correct anchors were genuinely deleted symbols — the resolver correctly distinguished dead code from moved code in every case.

## Gate decision

PASS at both horizons (94.8% single-commit, 88.0% at 200-commit zero-maintenance stress). The pre-registered kill condition (<60%) and inconclusive band (60-80%) were both cleared. Remaining engineering risks are now *named and bounded*: symbol renames (→ loud orphan + mechanical re-anchor) and generic-name ambiguity (→ fuzzy fingerprints, deeper qualification).

Caveats: regex extraction not tree-sitter (production should do better, not worse); Python-dominant sample; single-repo long-horizon.
