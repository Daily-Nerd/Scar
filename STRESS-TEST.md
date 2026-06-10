# SCAR — Stress Test

Adversarial analysis of my own idea. Part 1 is my self-attack. Part 2 is an independent adversarial review by a skeptic agent given the pitch blind, plus my responses.

---

## Part 1 — Self-attack

### 1.1 Cold start: an empty `.scars/` is worth exactly nothing

The value of SCAR on day one of any installation is zero, and the cost (one more tool, one more directory, one more review surface) is immediate. Classic knowledge-system death.

**Mitigations, honestly graded:**
- `scar harvest` bootstraps from git history — *real but overrated by me*. Harvest finds reverts and yanked dependencies; it cannot find the vendor phone call or the 3am decision. Bootstrap will be thin.
- Agent auto-authoring means scars accrue from normal work with no human effort — *this is the actual answer*, but it means the value curve starts near zero and compounds. Tools with compounding-only value need either patience or an immediate hook.
- **The immediate hook must be `scar why` on harvest output**: run `scar init && scar harvest` on a 5-year-old repo and get a readable "history of pain" report in 10 minutes. If that demo doesn't drop jaws, the cold-start problem is fatal. This is the first thing to validate.

### 1.2 Staleness: a wrong scar is worse than no scar

A scar that warns against something that's now fine actively blocks valid work and teaches editors to ignore all scars. Negative knowledge rots faster than positive knowledge — the dead end may have been a dead end only because of Postgres 12, the old vendor, the old traffic pattern.

**Mitigations:** expiry conditions and `review_after` are in the format; confidence decay; `scar challenge` with a real workflow. **Residual risk: high.** Decay parameters are guesses; nobody has data on the half-life of negative knowledge. This needs telemetry from real use, which needs adoption, which needs... see 1.1. Circular.

### 1.3 Alert fatigue: ten warnings per edit = zero warnings per edit

If every edit to a legacy file surfaces a wall of historical trauma, the hook gets disabled within a week.

**Mitigation baked into spec:** hard top-k=3 injection budget, 120-word cap, ranking by severity × confidence × anchor specificity. **Residual risk: medium.** The ranking function is load-bearing and unproven. Wrong ranking = right scars buried = same outcome as no scars.

### 1.4 Anchor rot

Symbols get renamed, files split, patterns drift. Orphaned scars pile up in a queue nobody triages — the system fills with loose knowledge attached to nothing, which is exactly the wiki-rot SCAR exists to prevent, now with extra steps.

**Mitigation:** orphaning is loud (CI, `scar status`), and re-anchoring is agent-delegable work ("here's the scar, here's the diff that orphaned it, propose new anchors"). **Honest assessment:** this is a genuinely hard engineering problem and the most likely source of "it kind of works but it's janky" reputation damage. Prototype must prove anchor survival across a real refactor before anything else gets built.

### 1.5 "Agents will just get better at mining git history themselves"

The strongest version of the obsolescence objection: a sufficiently good agent reads `git log`, finds the revert, infers the dead end. SCAR is just a cache of inferences the model can redo.

**Response, in three parts:**
1. Mining history per-edit is expensive (latency, tokens) and must be redone by every agent on every edit, forever. A scar is computed once and read in microseconds. Caches of expensive inferences with high hit rates are good businesses (that's what a CDN is).
2. **History mining has a hard ceiling: most negative knowledge never enters git.** The vendor email, the meeting decision, the incident channel. No amount of model capability recovers what was never committed.
3. The trend already visible by mid-2026: better models *raise* the value of structured context, not lower it — every agent vendor is racing to slurp more context per token. A pre-digested, repo-blessed, 120-word warning is exactly what that race wants.

**Where the objection lands anyway:** if agents get good enough at *cheap* history mining, harvest stops being a differentiator and only out-of-band knowledge + the injection protocol remain. Acceptable — that's still the core product.

### 1.6 Gaming: fencing your own bad code

A developer fences their mess against review pressure: "looks wrong, is intentional" as a universal shield.

**Mitigation:** scars require evidence links and pass through PR review; a fence with no incident/PR/commit behind it is challengeable on sight. **Residual risk: low-medium** — social, not technical, and identical in shape to comment abuse today, except scars are *more* visible and reviewable, not less.

### 1.7 "This is a feature, not a product" — the platform-absorption threat

GitHub, Cursor, or Anthropic ships "code context notes" natively and SCAR becomes a dead repo with a nice README. This is the most credible kill shot. Repowise already orbits the space from the heavyweight side; agent-memory vendors orbit from the other.

**Response:** probably *true at the product layer and irrelevant at the format layer.* The play is the open format — like `.editorconfig`, `CODEOWNERS`, `robots.txt`: small, boring, universal. Platforms absorbing the format is *success* (the knowledge gets captured; the format wins even if no company forms around it). The honest version of the business case: SCAR-the-format has high odds of mattering; SCAR-the-company has maybe 15-25% odds of clearing the feature-absorption bar via the org-graph layer. I would build it anyway, because I'm the primary beneficiary either way — see IDEA.md.

### 1.8 The quiet failure mode: nobody challenges anything

`scar challenge` assumes someone cares enough to contest stale knowledge. Realistic teams won't. Scars silently age, confidence decay quietly mutes them, and in 3 years `.scars/` is an archive nobody reads — the system degrades into exactly the wiki it replaced.

**Mitigation:** this is why decay + `review_after` are *defaults, not options* — the system must rot *gracefully and visibly* (muted scars still appear in `scar why`) rather than rot silently. Design principle: assume zero ongoing human maintenance; any value above that is upside.

---

## Part 2 — Independent skeptic review

*An adversarial review agent (the-skeptic) was given the full pitch and instructed to find fatal flaws, run a premortem, and identify the assumptions doing the most work. Findings below, followed by my responses.*

### 2.1 Skeptic verdict: 🔴 RECONSIDER

Ranked fatal-risk candidates from the independent review:

| Rank | Risk | Severity | Probability |
|------|------|----------|-------------|
| 1 | Agent-authored scars are low quality; review requirement kills the zero-friction claim | Fatal to core differentiator | High |
| 2 | GitHub Copilot Memory (default-on since 2026-03) already solves the job for most of the TAM | Market-timing kill | High — already shipped |
| 3 | Anchor rot / silent drift makes scars untrustworthy within ~6 months of active refactoring | Adoption killer | Medium-High |
| 4 | Lore protocol (arXiv:2603.15566) captures the "lighter alternative" narrative — git commit trailers, no new files at all | Narrative competition | Medium |
| 5 | OSS→paid path: no validated ICP, thin moat ("an engineer could build the org graph in a weekend") | Commercial failure | Medium-High |

### 2.2 Key skeptic findings, verbatim in substance

**A. The zero-friction authorship claim is the load-bearing assumption, and it forks into two bad cases.** If agents write scars freely, quality degrades and the hook gets disabled; if scars require human review, authorship cost isn't zero — it's *shifted* to a review queue, and adoption falls back to ADR-level behavior. No data exists on agent scar quality or false-positive rate.

**B. GitHub Copilot Memory shipped the same job-to-be-done in March 2026, default-on for Pro users.** Per GitHub's docs it stores "debugging history — root cause, fix, and what you ruled out along the way" and resurfaces it when similar symptoms appear. That *is* a deadend scar, with zero install friction and Microsoft distribution. The pitch's prior-art section missed it entirely.

**C. The Lore protocol (March 2026) is the invisible direct competitor.** It restructures git commit messages with native trailers to carry constraints, rejected alternatives, and agent directives — no new file format, no new directory, no CLI to install. "More git-native and more lightweight" than SCAR. In dev tooling, minimal usually wins when jobs overlap.

**D. Silent drift is worse than noisy staleness.** A scar whose anchor rots doesn't fire wrongly — it stops firing at all, giving false confidence. The premortem narrative: 60% of active scars stale within 6 months; the median developer dismisses one stale warning and then ignores all of them.

**E. What the plan gets right** (skeptic's words): the problem framing is "accurate and sharp" and verifiably worsening; enforcement-at-the-moment-of-action via PreToolUse injection is "the correct architectural instinct and genuinely differentiated from ADRs"; `scar harvest` is "the right answer to cold-start and the most creative technical element"; auto-authorship, *if* quality is solved, is "the correct lever on the only adoption bottleneck that has ever killed documentation systems."

### 2.3 My responses

**To A (agent scar quality):** Conceded as the critical experiment, not conceded as fatal. The spec already routes agent-authored scars to `candidates/` at reduced confidence — they never enter the active set without a human marking them kept. The honest reframe: authorship cost doesn't go to zero, it transforms from *recall + writing under no incentive* (historically ~never happens) to *reviewing a pre-written 10-line diff at PR time* (routinely happens — that's what code review is). Whether that transformation is enough is exactly Gate 4 in Part 3. The skeptic's 15% false-positive threshold is adopted as the quantitative bar.

**To B (Copilot Memory):** The sharpest hit, and it forces precision about what SCAR actually is. Copilot Memory is **private experience**: per-vendor, opaque, not in git, not reviewable, not portable, not contestable, gone if you switch tools. SCAR is **published law**: in the repo, reviewed in PRs, readable by every vendor's agent and every human, challengeable, with evidence attached. The same distinction as "an engineer's personal notebook" vs "the team's CODEOWNERS file" — both valuable, not substitutes. *But* the skeptic is right that for solo devs and small teams, private memory is good enough, and the TAM after Copilot Memory is genuinely smaller: multi-agent, multi-vendor teams who need knowledge that survives tool churn and shows up in review. That's a narrower wedge than the pitch claimed. Prior-art section updated; survey experiment ("has Copilot Memory solved this for you?") adopted as a pre-build gate.

**To C (Lore):** The correct response is interop, not combat. Lore trailers are append-only facts attached to *commits*; scars are living documents attached to *code regions* with lifecycle (challenge, expiry, re-anchor). Lore can't express a landmine (cross-file, bidirectional), can't be amended when the world changes (commits are immutable), and reading it back requires the expensive history walk SCAR exists to avoid. But Lore-formatted trailers are a *perfect harvest source*: `scar harvest` should parse Lore trailers natively, making SCAR the index over Lore rather than its rival. If the minimal format wins the authoring side, SCAR wins the query side. Added to roadmap.

**To D (silent drift):** Half-answered by spec (fingerprint mismatch → loud `orphaned` state in CI and `scar status`, never silent disappearance), half-open (who triages the orphan queue). The spec's bet: re-anchoring is mechanical, context-rich work — the single best task to delegate to an agent in CI. This is testable and is Gate 2.

**To E (commercial thesis):** No defense offered. The skeptic and my self-attack (§1.7) agree: SCAR-the-format is the high-probability bet, SCAR-the-company is a long shot that requires validation no one has done. The roadmap is restructured so that *zero* product-company work happens before the kill gates pass and 10 ICP interviews say otherwise. If it ends life as a beloved unmonetized OSS standard, that is a successful outcome for the problem and an acceptable one for me.

---

## Part 3 — Verdict criteria

Kill/continue gates, decided in advance so I can't move the goalposts later:

1. **Harvest demo gate:** `scar init && scar harvest && scar why` on 3 real aging repos must produce at least one "oh damn, I'd forgotten that" reaction per repo from someone who knows the repo. If harvest output reads as junk → the cold-start answer is dead → rethink or kill.
2. **Anchor survival gate:** ≥80% of anchors must survive a real-world refactor commit (rename + file split) without orphaning. Below that, the maintenance tax exceeds the value.
   **→ RUN 2026-06-09: ✅ PASSED — 94.8% across real rename/split commits, 88.0% across a 200-commit two-package-rename horizon with zero re-anchoring; path+line baseline died at 0.0%. Failures fully explained (one symbol-rename event + generic-name ambiguity). Full data: [experiments/anchor-survival/RESULTS.md](experiments/anchor-survival/RESULTS.md).**
3. **Fence honor gate (the one that matters):** instrumented A/B — an agent given a fence-protected file with the hook on vs off. The hook must measurably reduce fence-bulldozing without measurably degrading task completion. If agents ignore injected scars, the entire enforcement thesis is false.
   **→ RUN 2026-06-09: ✅ PASSED, 6/6 control violations vs 0/6 treatment, zero task degradation, scars overrode even explicit user instructions. Full data: [experiments/fence-honor/RESULTS.md](experiments/fence-honor/RESULTS.md).**
4. **Authorship gate:** in 2 weeks of real agent-assisted work on one repo, auto-authoring must produce ≥5 scars a human reviewer marks "worth keeping." Below that, the compounding-value engine doesn't compound.
