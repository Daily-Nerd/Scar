---
id: 3
type: landmine
title: CSV column order is parsed positionally downstream
severity: high
confidence: 0.9
created: 2025-08-30
authors: [mara@example.com]
anchors:
  - path: reports/export.py
  - symbol: export_transactions
evidence:
  - incident: INC-2025-0089
  - note: "consumer: recon-batch repo, finance team"
expires:
  condition: "recon-batch switches to header-based parsing"
  review_after: 2026-08-30
status: active
---

Finance's reconciliation pipeline (recon-batch, separate repo) parses this
CSV by column POSITION and ignores the header row. Changing column order
silently corrupts reconciliation — last time it took 11 days to detect
(INC-2025-0089).

The order txn_id,amount_cents,merchant_name,created_at,status,currency is
load-bearing. Refactor freely, but the emitted column order must not change.
