# Anchor Survival Replay — Results (Kill Gate 0.2)

Run: 2026-06-09. Real refactor history, no synthetic events. Protocol pre-registered (commit before run). Gate: ≥80% survival.

## Verdict: ✅ PASS — both horizons

| Test | Events | Survival | Baseline (path+line) |
|------|--------|----------|----------------------|
| **Single-commit replay** (TripWire 4 rename commits + Heimdall 1): package rename, subdir moves, file split | 197 anchors planted, 193 gated | **183/193 = 94.8%** | **0/197 = 0.0%** |
| **Long-horizon stress** (plant at envsync era → resolve at HEAD: ~200 commits later, TWO package renames, file split, CLI restructure, zero re-anchoring in between) | 137 anchors, 133 gated | **117/133 = 88.0%** | n/a (old paths all dead) |

The baseline number is the headline: **every single path+line anchor died** on the same events the SCAR resolver survived at 95%. Line-number anchors are not a simplification — they are zero.

## Shipped-mechanism re-validation (2026-07-01)

The numbers above were a **prototype** (regex symbol extraction + exact-string fingerprint) that never imported `src/scar`. After shipping the real mechanism — tree-sitter `scar.symbols` (`resolve_symbol` / `fingerprint` / `jaccard`, issues #94/#98 anchors and #99/#100 drift) — the same corpus + protocol were re-run through the shipped API (`replay_shipped.py`, #101).

| Test | Events | Shipped survival | Prototype | Baseline |
|------|--------|------------------|-----------|----------|
| Single-commit | 186 gated | **176/186 = 94.6%** | 94.8% | 0/197 = 0.0% |
| Long-horizon (200-commit) | 133 gated | **123/133 = 92.5%** | 88.0% | n/a |

- **The claim holds on shipped code**, not just the prototype. Single-commit matches within noise; long-horizon is *higher* because the shipped **Jaccard-similarity** disambiguation (argmax, no threshold) rescued all 6 cases the exact-hash prototype called AMBIGUOUS — the "use similarity, not equality" recommendation under *Secondary findings*, now empirically confirmed. The 10 FALSE-ORPHANs are the same single event (the `EnvSync`→`TripWire` class rename).
- **The measurement caught a real shipped bug.** The first shipped run left 26 TSX anchors `shipped_unsupported`: `scar.symbols` could not resolve TS/JS `const X = ...` / `export const Foo = () => {}` (name nested on `variable_declarator`). Fixed in `fix(symbols): resolve TS/JS const/arrow symbols` before the numbers above; unsupported dropped 26→7.
- **Disambiguation rule:** argmax Jaccard, tie-only AMBIGUOUS, **no similarity floor** (a floor would be an unvalidated tuned threshold — cf. #54). Long-horizon winning-Jaccard distribution: n=123, median 1.000, min 0.238 — the low tail is heavily-drifted-but-correctly-resolved symbols a floor would have wrongly killed.
- **Pinned corpus:** TripWire rename commits `04d16600`, `b2c3a88c`, `d73f53b3`; Heimdall `69f3816c`. Long-horizon: TripWire `04d1660~1` → HEAD over `src/envsync/`. Local one-shot measurement (depends on sibling `../TripWire` / `../Heimdall` clones), not CI-reproducible; small sample — directional confirmation, not a tight point estimate.

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

Caveats: ~~regex extraction not tree-sitter~~ **RESOLVED** — re-validated on the shipped tree-sitter mechanism 2026-07-01 (94.6% / 92.5%, see *Shipped-mechanism re-validation* above); Python-dominant sample (TSX now included after the const/arrow fix); single-repo long-horizon.
