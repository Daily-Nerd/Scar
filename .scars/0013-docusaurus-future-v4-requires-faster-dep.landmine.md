---
id: 13
type: landmine
title: Removing @docusaurus/faster while future.v4 is on kills the site build with a cryptic module error
severity: medium
confidence: 0.9
created: 2026-07-30
authors: ["claude-code", "Kibukx"]
anchors:
  - path: website/package.json
  - path: website/docusaurus.config.ts
evidence:
  - pr: 171
  - note: first build attempt failed: ERR_MODULE_NOT_FOUND thrown from @docusaurus/bundler/lib/importFaster.js via getCurrentBundler — no mention of @docusaurus/faster in the error; fixed by adding the dep pinned 3.10.2
expires:
  condition: "future.v4 removed from docusaurus.config.ts, or Docusaurus v4 makes the rspack bundler dependency explicit"
  review_after: 2027-01-30
status: active
---

`docusaurus.config.ts` sets `future: { v4: true }`, which switches the bundler
to rspack — imported at build time from `@docusaurus/faster`. That package is
referenced **nowhere** in the config, so it looks like an unused dependency in
`website/package.json`; removing it (or letting a dep-pruning tool drop it)
breaks `npm run build` with `ERR_MODULE_NOT_FOUND` deep inside
`@docusaurus/bundler/lib/importFaster.js`, never naming the missing package.

Keep `@docusaurus/faster` pinned to the exact same version as
`@docusaurus/core` and `preset-classic` (mixed minors across the three also
break the build). If you drop `future.v4` instead, then the dep really is
removable — remove both together or neither.
