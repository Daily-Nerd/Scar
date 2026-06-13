# Changelog

## [0.5.0](https://github.com/Daily-Nerd/Scar/compare/v0.4.0...v0.5.0) (2026-06-13)


### Features

* **harvest:** precision@N reporting CLI — close the measurement loop ([#53](https://github.com/Daily-Nerd/Scar/issues/53)) ([100bd1d](https://github.com/Daily-Nerd/Scar/commit/100bd1d46bbac981a3629b74c237fc0584f5ce05))
* **harvest:** ranking layer — heuristic scorer + label-capture instrument ([#39](https://github.com/Daily-Nerd/Scar/issues/39)) ([7369f73](https://github.com/Daily-Nerd/Scar/commit/7369f738d3fe356a0290cbf05f0654a48587ee9f))
* **lifecycle:** lint warns on evidence commit SHAs unreachable from HEAD ([#44](https://github.com/Daily-Nerd/Scar/issues/44)) ([714357e](https://github.com/Daily-Nerd/Scar/commit/714357e9b6366ec67d71d086cf62d8dafbcae976))
* **lifecycle:** orphan detection — resolution failure, loud in CI ([#34](https://github.com/Daily-Nerd/Scar/issues/34)) ([421a12a](https://github.com/Daily-Nerd/Scar/commit/421a12aae25cc46f6aa40593a6274bb755d4b81b))
* **lifecycle:** partial-anchor rot — surface dead anchors on firing scars ([#40](https://github.com/Daily-Nerd/Scar/issues/40)) ([85fd57e](https://github.com/Daily-Nerd/Scar/commit/85fd57e397055576bd754c3d606417274d6a9d5c))


### Bug Fixes

* **scars:** drop [#6](https://github.com/Daily-Nerd/Scar/issues/6) orphaned receipt, broaden scar [#5](https://github.com/Daily-Nerd/Scar/issues/5) for squash-merge ([#51](https://github.com/Daily-Nerd/Scar/issues/51)) ([4c63ac5](https://github.com/Daily-Nerd/Scar/commit/4c63ac50c648d8ec47190c6045987a276c9fb9bf))
* **scars:** re-anchor 3 ghost pattern anchors to real code ([#42](https://github.com/Daily-Nerd/Scar/issues/42)) ([00a2fcb](https://github.com/Daily-Nerd/Scar/commit/00a2fcb5c41c165f260019ec95bc636b18d17491))
* **scars:** replace 3 orphaned bare commit-SHA receipts with self-contained notes ([#46](https://github.com/Daily-Nerd/Scar/issues/46)) ([a224619](https://github.com/Daily-Nerd/Scar/commit/a224619f47387cce401039bf9ddbb93cb3841641))

## [0.4.0](https://github.com/Daily-Nerd/Scar/compare/v0.3.0...v0.4.0) (2026-06-12)


### Features

* **format:** reserve optional receipt_id field ([#29](https://github.com/Daily-Nerd/Scar/issues/29)) ([47ce933](https://github.com/Daily-Nerd/Scar/commit/47ce933cde02fa1155d0474e98101804cb7b1a80))


### Bug Fixes

* **hooks:** expose lifecycle commands ([#31](https://github.com/Daily-Nerd/Scar/issues/31)) ([dba2c0d](https://github.com/Daily-Nerd/Scar/commit/dba2c0d1c1bd8a0f73880bfab0ff17187eec2fb9)), closes [#30](https://github.com/Daily-Nerd/Scar/issues/30)


### Documentation

* **roadmap:** truth pass — gates resolved, Phase 1 shipped, Phase 2 in progress ([#26](https://github.com/Daily-Nerd/Scar/issues/26)) ([7701a97](https://github.com/Daily-Nerd/Scar/commit/7701a97610f470e7726e7f5fc86932a5101eb255))

## [0.3.0](https://github.com/Daily-Nerd/Scar/compare/v0.2.0...v0.3.0) (2026-06-12)


### Features

* **agents:** multi-agent scar integration — AGENTS.md, MCP server, agent helpers ([#21](https://github.com/Daily-Nerd/Scar/issues/21)) ([52c817f](https://github.com/Daily-Nerd/Scar/commit/52c817fc963f8f829b70de60b772c1097c6f0334))

## [0.2.0](https://github.com/Daily-Nerd/Scar/compare/v0.1.1...v0.2.0) (2026-06-12)


### Features

* **cli:** lifecycle v0 — challenge, archive, review_after surfacing ([#16](https://github.com/Daily-Nerd/Scar/issues/16)) ([0c6fb05](https://github.com/Daily-Nerd/Scar/commit/0c6fb05fbdbb57f8ac9b2a5b558e4cf121c3d5c0)), closes [#14](https://github.com/Daily-Nerd/Scar/issues/14)


### Documentation

* **readme:** scar challenge is planned, not shipped — point to lifecycle issue ([8c6b021](https://github.com/Daily-Nerd/Scar/commit/8c6b021c95299cf40bf6c2d978a0421bb9705cb6))

## [0.1.1](https://github.com/Daily-Nerd/Scar/compare/v0.1.0...v0.1.1) (2026-06-12)


### Bug Fixes

* **hooks:** drafter triggers on revert language only ([#12](https://github.com/Daily-Nerd/Scar/issues/12)) ([547c4bb](https://github.com/Daily-Nerd/Scar/commit/547c4bb21e3521682b6a4046602d6703d88c2cf1)), closes [#11](https://github.com/Daily-Nerd/Scar/issues/11)
