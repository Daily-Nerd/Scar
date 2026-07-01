# Changelog

## [0.11.0](https://github.com/Daily-Nerd/Scar/compare/v0.10.0...v0.11.0) (2026-07-01)


### Features

* **anchors:** re-measure anchor survival on the shipped mechanism (Phase 4) ([#102](https://github.com/Daily-Nerd/Scar/issues/102)) ([ae6c327](https://github.com/Daily-Nerd/Scar/commit/ae6c3279ea1fbe482bc202c664abb519aa3b7727))
* **harvest:** exclude lockfile basenames from comment-archaeology scan ([#87](https://github.com/Daily-Nerd/Scar/issues/87)) ([#104](https://github.com/Daily-Nerd/Scar/issues/104)) ([e30c002](https://github.com/Daily-Nerd/Scar/commit/e30c0029ff4aad95a219c668ce44e6bea1296906))


### Documentation

* **spec:** cut confidence dynamics from §5, document static weight ([#95](https://github.com/Daily-Nerd/Scar/issues/95)) ([#105](https://github.com/Daily-Nerd/Scar/issues/105)) ([96efe5f](https://github.com/Daily-Nerd/Scar/commit/96efe5fb583657a79ff3bf825351d97afe1802d5))

## [0.10.0](https://github.com/Daily-Nerd/Scar/compare/v0.9.0...v0.10.0) (2026-07-01)


### Features

* **anchors:** symbol-drift advisory (Phase 3) ([#100](https://github.com/Daily-Nerd/Scar/issues/100)) ([e384d23](https://github.com/Daily-Nerd/Scar/commit/e384d23f4105f778f2a01fbe711f148abe466aca))
* **anchors:** tree-sitter symbol anchors (Phases 1+2) ([#98](https://github.com/Daily-Nerd/Scar/issues/98)) ([f2c7a4e](https://github.com/Daily-Nerd/Scar/commit/f2c7a4eb5d463b80d94a705ea4d38366100fed4d))


### Bug Fixes

* correctness batch — 7 audit-verified bugs ([#93](https://github.com/Daily-Nerd/Scar/issues/93)) ([df0b6d7](https://github.com/Daily-Nerd/Scar/commit/df0b6d79fbd289e22ca6fedea634d347a0bf6423)), closes [#91](https://github.com/Daily-Nerd/Scar/issues/91)
* **match:** reject ReDoS-prone pattern anchors at the lint gate ([#89](https://github.com/Daily-Nerd/Scar/issues/89)) ([7c10fde](https://github.com/Daily-Nerd/Scar/commit/7c10fde191901ac531a0888f6c7dedf3f4d56364)), closes [#88](https://github.com/Daily-Nerd/Scar/issues/88)


### Documentation

* align SPEC/ROADMAP/README with shipped reality; lint-warn unsupported anchors ([#96](https://github.com/Daily-Nerd/Scar/issues/96)) ([76e14ce](https://github.com/Daily-Nerd/Scar/commit/76e14cee55518839381cd17753ad8c91dc45acd7)), closes [#90](https://github.com/Daily-Nerd/Scar/issues/90)
* **harvest:** document scorer type-prior as code-repo-calibrated ([#85](https://github.com/Daily-Nerd/Scar/issues/85)) ([cacf586](https://github.com/Daily-Nerd/Scar/commit/cacf5869005a7ec5f0510354629c3287610e6d3c)), closes [#54](https://github.com/Daily-Nerd/Scar/issues/54)

## [0.9.0](https://github.com/Daily-Nerd/Scar/compare/v0.8.0...v0.9.0) (2026-07-01)


### Features

* **cli:** Rich-colored --help via rich-argparse ([#81](https://github.com/Daily-Nerd/Scar/issues/81)) ([7983a85](https://github.com/Daily-Nerd/Scar/commit/7983a85a2069a548ffa676b8c07f5fe928f8971c)), closes [#80](https://github.com/Daily-Nerd/Scar/issues/80)

## [0.8.0](https://github.com/Daily-Nerd/Scar/compare/v0.7.0...v0.8.0) (2026-06-30)


### Features

* **cli:** add --version flag to report installed version ([#76](https://github.com/Daily-Nerd/Scar/issues/76)) ([e45805d](https://github.com/Daily-Nerd/Scar/commit/e45805d01d171e2264a37a102f73a5254d5cedeb)), closes [#75](https://github.com/Daily-Nerd/Scar/issues/75)
* **cli:** Rich output for read commands (TTY-detect + --json) ([#79](https://github.com/Daily-Nerd/Scar/issues/79)) ([3ff9d40](https://github.com/Daily-Nerd/Scar/commit/3ff9d4036138e7c898cae8dc18c9affc26553525))

## [0.7.0](https://github.com/Daily-Nerd/Scar/compare/v0.6.1...v0.7.0) (2026-06-30)


### Features

* durable evidence forms (issue:/url:) to survive squash-merge ([#50](https://github.com/Daily-Nerd/Scar/issues/50)) ([#74](https://github.com/Daily-Nerd/Scar/issues/74)) ([d558849](https://github.com/Daily-Nerd/Scar/commit/d558849652c068a9679617c5ed522b3eefffcc78))


### Bug Fixes

* **harvest:** exclude vendored/scaffold paths from comment-archaeology ([#72](https://github.com/Daily-Nerd/Scar/issues/72)) ([#73](https://github.com/Daily-Nerd/Scar/issues/73)) ([919add4](https://github.com/Daily-Nerd/Scar/commit/919add4b3503b65f866274d97995088a0aa8612a))
* **parser:** strip unquoted inline comments in _field ([#70](https://github.com/Daily-Nerd/Scar/issues/70)) ([03ec578](https://github.com/Daily-Nerd/Scar/commit/03ec578290f8dd8ce4ed436367a12eabbdb5c120))

## [0.6.1](https://github.com/Daily-Nerd/Scar/compare/v0.6.0...v0.6.1) (2026-06-26)


### Bug Fixes

* **plugin:** sync plugin.json + uv.lock to 0.6.0 + drift guard ([#63](https://github.com/Daily-Nerd/Scar/issues/63)) ([fe21ed6](https://github.com/Daily-Nerd/Scar/commit/fe21ed6046ea4cabc8abd2ca681e85d02991be6a))

## [0.6.0](https://github.com/Daily-Nerd/Scar/compare/v0.5.0...v0.6.0) (2026-06-26)


### Features

* **agent:** Packaged scar-authoring skill + Claude Code plugin ([#32](https://github.com/Daily-Nerd/Scar/issues/32)) ([#58](https://github.com/Daily-Nerd/Scar/issues/58)) ([b1eaca7](https://github.com/Daily-Nerd/Scar/commit/b1eaca7f702d86a4e5429c8c085c1537f5505d0d))


### Bug Fixes

* **harvest:** exclude .scars/ tree from candidates (self-ref noise) ([#56](https://github.com/Daily-Nerd/Scar/issues/56)) ([29c2d61](https://github.com/Daily-Nerd/Scar/commit/29c2d6116e23a748df8455170c311023c6578c5c)), closes [#55](https://github.com/Daily-Nerd/Scar/issues/55)

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
