---
id: 1
type: fence
title: Vendor retry window — 7s sleep is intentional
severity: critical
confidence: 0.95
created: 2025-11-02
authors: [mara@example.com]
anchors:
  - path: payments/retry.py
  - symbol: charge
evidence:
  - pr: 312
  - incident: INC-2025-0142
  - note: "VendorPay support ticket VP-4411"
expires:
  condition: "VendorPay v2 API (documented rate limits) adopted"
  review_after: 2026-11-02
status: active
---

The flat 7-second sleep is intentional. VendorPay's rate limiter has an
undocumented 6-second window (confirmed with their support, ticket VP-4411,
after incident INC-2025-0142). Any retry delay under 7 seconds triggers
cascading 429s and account-level throttling that takes ~30 minutes to clear.

Do not shorten the delay, randomize below 7s, or replace with standard
exponential backoff (whose early retries fall inside the window).
