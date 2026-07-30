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


def test_unknown_tool_raises_clean_error():
    import pytest
    with pytest.raises(ValueError, match="unknown tool"):
        _handle("tools/call", {"name": "scar_nuke", "arguments": {}})


def test_draft_default_confidence_matches_model_default(tmp_path):
    from scar.model import Scar, parse_scar_text
    init_scars(tmp_path)
    _handle("tools/call", {
        "name": "scar_draft",
        "arguments": {
            "repo": str(tmp_path), "type": "deadend", "title": "Defaults check",
            "anchors": [{"path": "src/"}], "body": "Body.",
        },
    })
    cand = next((tmp_path / ".scars" / "candidates").glob("defaults-check*.md"))
    drafted = parse_scar_text(cand.read_text())
    assert drafted.confidence == Scar().confidence


def test_server_version_matches_package():
    from importlib.metadata import version
    info = _handle("initialize", {})
    assert info["serverInfo"]["version"] == version("scar-cli")


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


from scar.mcp import TOOLS


def test_scar_draft_description_carries_authoring_digest():
    draft = next(t for t in TOOLS if t["name"] == "scar_draft")
    desc = draft["description"]
    for kind in ("deadend", "fence", "landmine"):
        assert kind in desc
    assert "candidate" in desc


# --- stdio transport (#162): MCP spec wants newline-delimited JSON ---

def _fake_std(data: bytes = b""):
    import io
    from types import SimpleNamespace
    return SimpleNamespace(buffer=io.BytesIO(data))


def test_write_message_emits_newline_delimited_json(monkeypatch):
    import sys
    from scar.mcp import _write_message
    out = _fake_std()
    monkeypatch.setattr(sys, "stdout", out)
    _write_message({"jsonrpc": "2.0", "id": 1, "result": {}})
    raw = out.buffer.getvalue()
    assert raw == b'{"jsonrpc":"2.0","id":1,"result":{}}\n'
    assert b"Content-Length" not in raw


def test_read_message_parses_a_json_line(monkeypatch):
    import sys
    from scar.mcp import _read_message
    monkeypatch.setattr(
        sys, "stdin",
        _fake_std(b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n'))
    assert _read_message() == {"jsonrpc": "2.0", "id": 7, "method": "ping"}


def test_read_message_skips_blank_lines_and_returns_none_at_eof(monkeypatch):
    import sys
    from scar.mcp import _read_message
    monkeypatch.setattr(sys, "stdin", _fake_std(b'\n\n{"id":1}\n'))
    assert _read_message() == {"id": 1}
    assert _read_message() is None


def test_serve_subprocess_completes_real_handshake(tmp_path):
    import subprocess
    import sys
    requests = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        b'{"protocolVersion":"2025-06-18","capabilities":{}}}\n'
        b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    )
    proc = subprocess.run(
        [sys.executable, "-c", "from scar.mcp import serve; raise SystemExit(serve())"],
        input=requests, capture_output=True, timeout=15, cwd=tmp_path)
    assert proc.returncode == 0
    lines = [line for line in proc.stdout.split(b"\n") if line.strip()]
    assert len(lines) == 2
    init = json.loads(lines[0])
    assert init["id"] == 1
    assert init["result"]["serverInfo"]["name"] == "scar"
    tools = json.loads(lines[1])
    assert tools["id"] == 2
    assert [t["name"] for t in tools["result"]["tools"]] == [
        "scar_query", "scar_why", "scar_draft"]
