# SCAR — Roadmap

Restructured after the adversarial review (see [STRESS-TEST.md](STRESS-TEST.md)): **validation before construction**. Every phase had a kill gate decided in advance. Phase 0 and Phase 1 are complete; Phase 2 is in progress. Strategy note (2026-06-11): SCAR is published as **OSS-as-gift** — personal infrastructure shared openly, no product promises. Product work only happens if organic stranger traction appears; that decision retired gate 0.5.

## Phase 0 — Kill-gate experiments ✅ complete

| # | Experiment | Gate (pass/kill) | Result |
|---|-----------|------------------|--------|
| 0.1 | Prototype `scar harvest` (reverts, add-then-remove deps, reopened issues, comment archaeology); run on 5 real aging repos | ≥1 "I'd forgotten that" reaction per repo from someone who knows it; harvest precision subjectively >50% | ✅ **PASSED 2026-06-09** on a 7-year-old personal infra repo — owner had forgotten a service-placement deadend the harvest resurfaced; 12/12 curated candidates correct. Raw precision 13% → ranking layer remains a requirement (Phase 2). |
| 0.2 | Prototype anchors (tree-sitter symbols + content fingerprints); replay real historical refactor commits against them | ≥80% anchor survival across rename + file-split refactors | ✅ **PASSED 2026-06-09** — 94.8% single-commit, 88.0% at 200-commit zero-maintenance horizon; naive path+line baseline 0.0% ([results](experiments/anchor-survival/RESULTS.md)) |
| 0.3 | **Fence honor test**: hand-write fences on a real repo, wire a PreToolUse hook, A/B agent sessions hook-on vs hook-off on tasks that tempt fence-bulldozing | Hook measurably reduces fence violations without degrading task completion | ✅ **PASSED 2026-06-09** — 6/6 control violations vs 0/6 treatment ([results](experiments/fence-honor/RESULTS.md)) |
| 0.4 | Auto-authorship trial: 2 weeks of normal agent-assisted work with the stop-hook drafting `deadend` candidates | ≥5 human-kept scars; false-positive rate <15% (skeptic's bar, adopted) | ✅ **PASSED 2026-06-11**, closed day 3 of 14 — 13 keepable agent-authored scars across 3 repos, 0% rejected. One agent-authored scar caught a real parser bug and fired on the exact edit that fixed it. Drafter *trigger* precision was tuned separately (revert-language-only) after 3 of 6 firings proved false. |
| 0.5 | Survey 50 Claude Code / Cursor users re: Copilot Memory | Meaningful segment says no + wants repo-resident, reviewable knowledge | ⛔ **RETIRED 2026-06-11** — the OSS-as-gift decision removed the product hypothesis this gate validated. If stranger traction ever reopens the product question, this survey reopens with it. |
| 0.6 | Implement Lore trailers on the same repo as 0.3; compare agent behavior vs scar injection | SCAR injection outperforms history-walk over trailers on latency and compliance | ⏸ **Deprioritized** — no longer gating anything; optional research. |

## Phase 1 — Format + CLI (OSS) ✅ shipped

Public at [github.com/Daily-Nerd/Scar](https://github.com/Daily-Nerd/Scar), `scar-cli` on PyPI. Honest deltas from the original plan:

- ✅ `SCAR-FORMAT.md` v0.1 published; one parser/serializer (`model.py`), one renderer (`render.py`)
- ✅ CLI: `init, lint, status, promote, check, why, challenge, archive, harvest, hook, mcp, agent, inject` — lifecycle commands (`challenge`/`archive`, expiry review surfacing) shipped beyond the original plan
- ✅ Claude Code plugin: PreToolUse injection + stop-hook candidate drafting, both field-validated (gates 0.3, 0.4)
- ⚠️ **Python, not Go/Rust** — zero-dependency stdlib hits the goal the compiled binary was chasing (~20ms hook startup, trivial install via `uv tool install scar-cli`); a rewrite is not planned unless profiling says otherwise
- ❌ No `add` command — copy `template.md` + `scar promote` covers authoring; revisit only on user friction
- ❌ Lore trailer ingestion in `harvest` — moved to Phase 2, optional
- ✅ Dogfooding: 6 repos, including this one (the repo's own scars caught its own release-process bug)

## Phase 2 — Ecosystem 🔄 in progress

- ✅ **MCP server** (`scar_query`, `scar_why`, `scar_draft`) — shipped v0.3.0, dependency-free stdio, drafts gated to candidates/. First non-Claude agent (Codex) arrived and contributed the implementation — the deferral condition resolving itself.
- ✅ Multi-agent surface: committed `AGENTS.md`, `scar inject --diff`, `scar agent doctor/config` for Codex, Cursor, Windsurf, opencode (v0.3.0)
- 🔶 CI surface: expiry warnings shipped (`lint`/`status`, v0.2.0); **orphan detection is the next milestone** — content-fingerprint drift → `orphaned` status, loud in CI (principle 3 is not yet enforced by code)
- ⬜ Harvest ranking layer (gate 0.1 verdict: required — raw precision 13% without it)
- ⬜ Re-anchoring agent workflow: orphaned scar + orphaning diff → proposed new anchors as a PR
- ⬜ Editor surfaces (VS Code gutter marks, LSP code lens) — fences visible to humans, not only agents
- ⬜ Lint warning on evidence commit SHAs unreachable from HEAD (scar #5's expiry condition)

## Phase 3 — The org graph ⏸ parked by design

The commercial hypothesis (cross-repo aggregation, recurrence analytics, policy, managed harvest) is **parked under the OSS-as-gift decision**, not killed: it reopens only on organic adoption signal — external repos with active scars, inbound interest from strangers. "SCAR remains a free standard" was declared in advance as an acceptable ending, and it is the current operating assumption.

## Non-negotiable principles carried from the stress test

1. Advisory by default, forever. Blocking is opt-in, per-scar-severity, in CI only.
2. Max 3 scars / ~120 words each injected per edit. The fatigue budget is a format-level guarantee, not a tuning knob (enforced in `render.py`).
3. Rot must be loud. No scar ever disappears silently; orphaning is a visible state. *(Lifecycle transitions enforce this for human decisions; orphan detection — the code-drift half — is Phase 2's next milestone.)*
4. Assume zero ongoing human maintenance; design for graceful visible decay.
5. The format stays open and vendor-neutral even if a company forms. Platform absorption of the format = success, not failure.
