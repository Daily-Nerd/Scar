---
id: 2
type: deadend
title: Session caching — tried twice, both incidents
severity: critical
confidence: 0.9
created: 2024-10-19
authors: [devon@example.com, "claude-code"]
anchors:
  - path: services/sessions.py
  - pattern: "redis|memcache|cachetools|TTLCache|lru_cache"
evidence:
  - pr: 1482
  - incident: INC-2024-0231
expires:
  condition: "sessions become stateless / re-derivable from auth tokens"
  review_after: 2026-10-19
status: active
---

We ran cached sessions twice: Redis (2024-Q1) and an in-process TTL cache
(2024-Q3). Both served stale or missing auth state. Redis eviction under
memory pressure logged users out mid-checkout (INC-2024-0231, ~$40k
attributed loss). Sessions are not re-derivable, so eviction is data loss,
not a cache miss.

Do not add any caching or evicting layer for session data. Postgres-only
is intentional.
