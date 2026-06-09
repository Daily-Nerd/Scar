# Harvest — Curated Candidates (homelab-apps)

Raw report: [report-homelab-apps.md](report-homelab-apps.md) — 91 candidates from 6 heuristics over 470 commits (2025-03 → 2026-06).

Manual curation pass (what `scar harvest` ranking would need to automate). Top candidates, strongest first:

| # | Proposed scar | Type | Evidence |
|---|--------------|------|----------|
| 1 | **LiteLLM must not schedule on bifrost** — spread across ragnarok hosts only | deadend | `b706243` revert(litellm); reverted an explicit placement attempt |
| 2 | **Authentik upgrades are staged, never direct** — 2026.2.2 direct boot failed; pinned back to 2025.10.0, then fresh boot on wiped schema; PG12→PG16 migration en route | deadend | `c15b353` downgrade, `047998c` wiped schema, `5c47bd5`/`5d89244` scale-backs |
| 3 | **Authentik pg_hba.conf: copy via initContainer on restart only, NOT first init** — direct mount broke; first-init copy broke differently | landmine | fix-chain `5f67b48`, `ad48a01`, probe-delay fixes (NFS slow init) |
| 4 | **qBittorrent: Gluetun VPN sidecar is a dead end** — image flapped qbittorrent↔gluetun same day, then container removed, firewall rules reworked twice | deadend | `78b3fb8`, `4eda6ea`, `da2780c` |
| 5 | **Image tags must be pinned to digest, never floating** — repo-wide remediation happened once already | fence | `66257a6` "pin all floating image tags + busybox to digest (#61)" |
| 6 | **heimdall containers: runAsUser 1000 must stay pinned** — image USER is non-numeric, k8s can't resolve it | fence | `b75b6d5` (#104) |
| 7 | **In-cluster Prometheus was abandoned after a 9-fix day** — config volume comment/uncomment war, replicas thrash, deleted 2026-04 | deadend | H5 chain 2025-09-25 ×9, deletion `42e41f8` |
| 8 | **Full media stack on k8s abandoned** (jellyfin/sonarr/radarr/prowlarr/qbittorrent/jellyseerr, 2026-01 → 2026-04) | deadend | `d662f20` wholesale removal |
| 9 | **zigbee2mqtt config comes from restored file, not ConfigMap** | fence | `11306e2`, PVC rename `6ffe804` |
| 10 | **hermes-agent is single-instance by design** — replicas flapped 0↔1 with "single-user agent" reasoning both directions | fence | `3300655`, `f1425e1` |
| 11 | **tailscale connector resources: 100m/128Mi is the settled value** — raised to 500m/256Mi, reverted 7 months later | fence (weak) | `0933581`, `82d2bf7` |
| 12 | **Longhorn: defaultReplicaCount must be 1 on this cluster** — and Longhorn itself was previously removed once (2025-03→09) | fence + deadend | `bb52326`, `f14671f` |

## Noise assessment (honest)

- ~34 H4 flaps: majority are routine ops scaling (`replicas 1→0→1` for migrations/cutovers) — **noise**, need suppression rule: flap whose middle state is `0` + commit mentions migration/cutover/restore = operational, not knowledge.
- H3: 26 deletions but ~8 distinct events (kustomize multi-file inflation) — need event grouping.
- H6 (comment archaeology) returned zero — pattern/filter bug or genuinely comment-poor YAML repo; needs fixing before the heuristic can be judged.
- Curated yield: **~12 real candidates / 91 raw ≈ 13% precision raw, but ~100% of the 12 look genuinely scar-worthy.** The harvest thesis holds only WITH a ranking/curation layer — raw output would fail the noise test on day one. This matches SPEC.md §6's "precision over recall" warning and makes the ranking layer a v0 requirement, not a nice-to-have.

## Gate 0.1 verdict: ✅ PASSED (2026-06-09)

Pass condition: ≥1 "damn, I'd forgotten that" from someone who knows the repo.

Repo owner's judgment: **"I honestly forgot about this issue: LiteLLM must not schedule on bifrost — spread across ragnarok hosts only. All look correct at quick glance."**

- ≥1 forgotten-knowledge hit: ✓ (candidate #1 — and the owner had the litellm config open in their editor at the time, i.e., the scar would have been *live-relevant* that very session)
- No false positives identified in the curated 12 (quick-glance review): ✓
- Caveat carried forward: pass applies to the CURATED list. Raw output (13% precision) would have failed. Ranking/curation layer is confirmed as a v0 requirement.
