"""MCP tool dispatch."""

import json

from scar.mcp import _handle
from scar.store import init_scars

FENCE = """\
---
id: 1
type: fence
title: Sleep is 7s for vendor window
severity: critical
confidence: 0.9
created: 2026-06-09
authors: [mara]
anchors:
  - path: payments/
evidence:
  - commit: aaa1111
status: active
---

Do not lower the sleep.
"""


def _text(result):
    return result["content"][0]["text"]


def test_tools_list_exposes_scar_tools():
    result = _handle("tools/list", {})
    assert [t["name"] for t in result["tools"]] == [
        "scar_query", "scar_why", "scar_draft"]


def test_scar_query_returns_structured_matches(tmp_path):
    init_scars(tmp_path)
    (tmp_path / ".scars" / "0001-vendor.fence.md").write_text(FENCE)
    result = _handle("tools/call", {
        "name": "scar_query",
        "arguments": {"repo": str(tmp_path), "path": "payments/retry.py"},
    })
    data = json.loads(_text(result))
    assert data["matches"][0]["title"] == "Sleep is 7s for vendor window"
    assert data["matches"][0]["matched_by"] == ["path"]


def test_scar_draft_writes_candidate_only(tmp_path):
    init_scars(tmp_path)
    result = _handle("tools/call", {
        "name": "scar_draft",
        "arguments": {
            "repo": str(tmp_path),
            "type": "deadend",
            "title": "Redis failed here",
            "anchors": [{"pattern": "redis"}],
            "body": "Redis was tried and abandoned.",
        },
    })
    data = json.loads(_text(result))
    assert data["status"] == "candidate"
    assert data["candidate"].startswith(".scars/candidates/")
    assert not list((tmp_path / ".scars").glob("*.deadend.md"))
