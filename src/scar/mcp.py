"""Minimal MCP stdio server for SCAR.

No runtime dependencies: the CLI remains installable as a tiny local tool, and
MCP hosts can launch it with `scar mcp`.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from .lint import lint_text
from .match import rank_matches_for_diff, rank_matches_for_paths
from .model import Scar
from .store import ScarStore


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("scar-cli")
    except Exception:  # uninstalled source checkout — version is cosmetic here
        return "0.0.0"


def _store(repo: str | None) -> ScarStore:
    start = Path(repo).expanduser().resolve() if repo else Path.cwd()
    store = ScarStore.discover(start)
    if store is None:
        raise ValueError("no .scars/ directory found")
    return store


def _read_message() -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if line == b"":
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("ascii", errors="ignore").partition(":")
        headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return {}
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _write_message(payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(body)
    sys.stdout.buffer.flush()


def _text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _json_result(data: Any) -> dict[str, Any]:
    return _text_result(json.dumps(data, indent=2))


def _scar_from_args(args: dict[str, Any]) -> Scar:
    anchors = args.get("anchors") or []
    path_anchors = []
    pattern_anchors = []
    for anchor in anchors:
        if isinstance(anchor, dict):
            if anchor.get("path"):
                path_anchors.append(str(anchor["path"]))
            if anchor.get("pattern"):
                pattern_anchors.append(str(anchor["pattern"]))
    evidence = []
    for item in args.get("evidence") or []:
        if isinstance(item, dict) and len(item) == 1:
            key, value = next(iter(item.items()))
            evidence.append(f"{key}: {value}")
        else:
            evidence.append(str(item))
    return Scar(
        type=str(args.get("type", "deadend")),
        title=str(args.get("title", "")),
        severity=str(args.get("severity", Scar.severity)),
        confidence=float(args.get("confidence", Scar.confidence)),
        created=str(args.get("created") or time.strftime("%Y-%m-%d")),
        authors=[str(a) for a in args.get("authors", ["agent"])],
        path_anchors=path_anchors,
        pattern_anchors=pattern_anchors,
        evidence=evidence,
        expires_condition=str(args.get("expires_condition", "")),
        review_after=str(args.get("review_after", "")),
        status="candidate",
        body=str(args.get("body", "")).strip(),
    )


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "scar-candidate"


def _draft(args: dict[str, Any]) -> dict[str, Any]:
    store = _store(args.get("repo"))
    scar = _scar_from_args(args)
    findings = lint_text(scar.to_text())
    errors = [f.message for f in findings if f.level == "error"]
    if errors:
        raise ValueError("refusing to draft invalid scar: " + "; ".join(errors))
    candidates = store.scars_dir / "candidates"
    candidates.mkdir(exist_ok=True)
    stem = _slug(str(args.get("slug") or scar.title))
    path = candidates / f"{stem}.md"
    i = 2
    while path.exists():
        path = candidates / f"{stem}-{i}.md"
        i += 1
    path.write_text(scar.to_text(), encoding="utf-8")
    return _json_result({"candidate": str(path.relative_to(store.root)),
                         "status": "candidate"})


def _query(args: dict[str, Any]) -> dict[str, Any]:
    store = _store(args.get("repo"))
    top_k = int(args.get("top_k", 3))
    if args.get("diff"):
        matches = rank_matches_for_diff(store, str(args["diff"]), top_k=top_k)
    else:
        paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
        matches = rank_matches_for_paths(store, [str(p) for p in paths],
                                         str(args.get("content", "")), top_k=top_k)
    return _json_result({"matches": [m.to_dict() for m in matches],
                         "broken": [str(p.relative_to(store.root))
                                    for p in store.broken()]})


def _why(args: dict[str, Any]) -> dict[str, Any]:
    store = _store(args.get("repo"))
    rel = str((store.root / str(args.get("path", "."))).resolve().relative_to(store.root))
    records = [{"source": str(source.relative_to(store.root)), **scar.__dict__}
               for source, scar in store.scars_for_path(rel)]
    return _json_result({"path": rel, "records": records})


TOOLS = [
    {
        "name": "scar_query",
        "description": "Return ranked negative-knowledge scars for paths, content, or a unified diff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "paths": {"type": "array", "items": {"type": "string"}},
                "content": {"type": "string"},
                "diff": {"type": "string"},
                "top_k": {"type": "integer", "default": 3},
            },
        },
    },
    {
        "name": "scar_why",
        "description": "Return all scar history anchored to a path, including non-firing records.",
        "inputSchema": {
            "type": "object",
            "properties": {"repo": {"type": "string"}, "path": {"type": "string"}},
        },
    },
    {
        "name": "scar_draft",
        "description": "Write a candidate scar to .scars/candidates/ for human promotion.",
        "inputSchema": {
            "type": "object",
            "required": ["type", "title", "anchors", "body"],
            "properties": {
                "repo": {"type": "string"},
                "type": {"type": "string", "enum": ["deadend", "fence", "landmine"]},
                "title": {"type": "string"},
                "severity": {"type": "string"},
                "confidence": {"type": "number"},
                "authors": {"type": "array", "items": {"type": "string"}},
                "anchors": {"type": "array", "items": {"type": "object"}},
                "evidence": {"type": "array"},
                "expires_condition": {"type": "string"},
                "review_after": {"type": "string"},
                "body": {"type": "string"},
                "slug": {"type": "string"},
            },
        },
    },
]


def _handle(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "scar", "version": _version()},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        tools = {"scar_query": _query, "scar_why": _why, "scar_draft": _draft}
        if name not in tools:
            raise ValueError(f"unknown tool: {name}")
        return tools[name](args)
    if method == "ping":
        return {}
    if method.startswith("notifications/"):
        return None
    raise ValueError(f"unsupported method: {method}")


def serve() -> int:
    while True:
        request = _read_message()
        if request is None:
            return 0
        if "id" not in request:
            continue
        try:
            result = _handle(str(request.get("method", "")), request.get("params") or {})
            if result is not None:
                _write_message({"jsonrpc": "2.0", "id": request["id"], "result": result})
        except Exception as exc:
            _write_message({"jsonrpc": "2.0", "id": request["id"],
                            "error": {"code": -32000, "message": str(exc)}})
