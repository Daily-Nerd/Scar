---
id: 3
type: landmine
title: Hook installer binds to whatever PATH resolves — active venv silently re-pins hooks to .venv/bin/scar
severity: high
confidence: 0.9
created: 2026-06-11
authors: ["claude-code", "Kibukx"]
anchors:
  - path: hook/scar-hooks.py
  - pattern: "shutil\\.which\\([\"']scar[\"']\\)"
evidence:
  - note: 2026-06-11 session: user ran `source .venv/bin/activate` then `python3 fabcap/hook/scar-hooks.py install` intending to rebind global hooks to ~/.local/bin/scar; installer reported 'up-to-date' and left all 3 hooks on fabcap/.venv/bin/scar
status: active
---

`install()` resolves the scar binary with `shutil.which("scar")` and writes that
absolute path into `~/.claude/settings.json` for all three hooks. The result is
PATH-order dependent: with the fabcap venv activated (or cwd inside fabcap under
some shells), `which` returns `.venv/bin/scar`, the desired entries match the
existing ones exactly, and the installer prints "up-to-date" — looking like a
successful rebind while changing nothing. Deleting `.venv` later kills all global
hooks silently. This bit the same user twice in two days. Until `find_scar()`
filters out `$VIRTUAL_ENV` paths, the install must be run with no venv active
(`deactivate` first) and verified by checking the printed "route through" path
ends in `~/.local/bin/scar`.
