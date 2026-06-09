# SCAR — Roadmap

Restructured after the adversarial review (see [STRESS-TEST.md](STRESS-TEST.md)): **validation before construction**. Every phase has a kill gate decided in advance. No product-company work happens before Phase 2 gates pass.

## Phase 0 — Kill-gate experiments (≈4-6 weeks, no product code beyond throwaway prototypes)

| # | Experiment | Gate (pass/kill) | Cost |
|---|-----------|------------------|------|
| 0.1 | Prototype `scar harvest` (reverts, add-then-remove deps, reopened issues, comment archaeology); run on 5 real aging repos | ≥1 "I'd forgotten that" reaction per repo from someone who knows it; harvest precision subjectively >50% | 1 week |
| 0.2 | Prototype anchors (tree-sitter symbols + content fingerprints); replay real historical refactor commits against them | ≥80% anchor survival across rename + file-split refactors | 1 week |
| 0.3 | **Fence honor test**: hand-write fences on a real repo, wire a PreToolUse hook, A/B agent sessions hook-on vs hook-off on tasks that tempt fence-bulldozing | Hook measurably reduces fence violations without degrading task completion | ✅ **PASSED 2026-06-09** — 6/6 control violations vs 0/6 treatment ([results](experiments/fence-honor/RESULTS.md)) |
| 0.4 | Auto-authorship trial: 2 weeks of normal agent-assisted work with the stop-hook drafting `deadend` candidates | ≥5 human-kept scars; false-positive rate <15% (skeptic's bar, adopted) | 2 weeks, overlaps |
| 0.5 | Survey 50 Claude Code / Cursor users: "Has Copilot Memory solved re-tried dead ends / bulldozed fences for you?" | Meaningful segment says no + wants repo-resident, reviewable knowledge | 3 days |
| 0.6 | Implement Lore trailers on the same repo as 0.3; compare agent behavior vs scar injection | SCAR injection outperforms history-walk over trailers on latency and compliance | 1 week |

**Any of 0.2, 0.3, 0.4 failing = stop and rethink the architecture. 0.5 failing = the wedge is gone; archive the project with its own deadend scar.** (The repo eating its own dog food on the way out.)

## Phase 1 — Format + CLI (OSS), only after Phase 0 passes

- `SCAR-FORMAT.md` v0.1 published as a spec independent of the tool — the format is the long-term bet.
- `scar` CLI: `init`, `add`, `check`, `why`, `status`, `harvest`, `inject` (Go or Rust; single static binary; <150ms `inject` budget with incremental index under `.git/scar-index`).
- Claude Code plugin: PreToolUse injection + stop-hook candidate drafting.
- Lore trailer ingestion in `harvest` (interop, per stress-test response C).
- Dogfood on this repo and 2-3 friendly real projects.

## Phase 2 — Ecosystem

- MCP server (`scar_query`, `scar_why`, `scar_draft`).
- CI surface: orphan/expiry warnings; opt-in `--strict` for `critical` scars.
- Re-anchoring agent workflow: orphaned scar + orphaning diff → proposed new anchors as a PR.
- Editor surfaces (VS Code gutter marks, LSP code lens) — fences visible to humans, not only agents.
- **Gate to Phase 3:** organic adoption signal (external repos with active scars, inbound interest) + 10 ICP interviews validating org-level pain at a price point. Skeptic risk #5 stands until then.

## Phase 3 — The org graph (commercial hypothesis, explicitly unproven)

- Cross-repo scar aggregation: "this dead end has been hit by 4 teams."
- Recurrence analytics; policy ("payment-path changes must acknowledge fences"); managed harvest.
- Only if Phase 2's gate passed. Otherwise SCAR remains a free standard, and that is a declared-in-advance acceptable ending.

## Non-negotiable principles carried from the stress test

1. Advisory by default, forever. Blocking is opt-in, per-scar-severity, in CI only.
2. Max 3 scars / ~120 words each injected per edit. The fatigue budget is a format-level guarantee, not a tuning knob.
3. Rot must be loud. No scar ever disappears silently; orphaning is a visible state.
4. Assume zero ongoing human maintenance; design for graceful visible decay.
5. The format stays open and vendor-neutral even if a company forms. Platform absorption of the format = success, not failure.
