"""Fake signer and checker programs for the presence tests (#392).

Never the real binaries: a fake is a small script at a tmp path that answers
canned replies per command and logs every call (argv and stdin) so a test can
assert exactly what was sent and in what order. Tests point presence.SIGNER and
presence.CHECKER at these.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPT = """#!{python}
import base64, hashlib, json, sys, time
from pathlib import Path
here = Path(__file__).parent
name = Path(__file__).name
cfg = json.loads((here / (name + ".cfg.json")).read_text())
stdin = sys.stdin.read()
cmd = sys.argv[1] if len(sys.argv) > 1 else ""
state_file = here / (name + ".state.json")
state = json.loads(state_file.read_text()) if state_file.exists() else {{}}
n = state.get(cmd, 0)
state[cmd] = n + 1
state_file.write_text(json.dumps(state))
with open(here / (name + ".calls.jsonl"), "a") as fh:
    fh.write(json.dumps({{"cmd": cmd, "argv": sys.argv[1:], "stdin": stdin}}) + "\\n")
replies = cfg.get(cmd) or cfg.get("*") or [{{"out": "{{\\"error\\":\\"unsupported_command\\"}}", "code": 2}}]
r = replies[min(n, len(replies) - 1)]
time.sleep(r.get("sleep", 0))
if r.get("act_from_decision"):
    # Like the real signer, bind the act to the record it was handed: inputs_hash
    # is "u" + b64url(0x12 0x20 + sha256(decision bytes)), worked out here with
    # hashlib alone so a test proves the bytes promote sends are the bytes Scar
    # hashes later.
    record = base64.b64decode(json.loads(stdin)["attachments"]["decision_b64"])
    digest = b"\\x12\\x20" + hashlib.sha256(record).digest()
    claims = {{"inputs_hash": "u" + base64.urlsafe_b64encode(digest).decode().rstrip("=")}}
    seg = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    body = json.loads(r["out"])
    body["jws"] = seg(b'{{"alg":"fake"}}') + "." + seg(json.dumps(claims).encode()) + "." + seg(b"sig")
    r = {{**r, "out": json.dumps(body, sort_keys=True)}}
sys.stdout.write(r["out"] + "\\n")
sys.exit(r.get("code", 0))
"""


class FakeBinary:
    def __init__(self, directory: Path, name: str):
        self.path = directory / name
        self._cfg = directory / (name + ".cfg.json")
        self._calls = directory / (name + ".calls.jsonl")
        self.path.write_text(_SCRIPT.format(python=sys.executable))
        self.path.chmod(0o755)
        self._cfg.write_text("{}")
        self.config: dict[str, list[dict]] = {}

    def reply(self, command: str, *replies) -> "FakeBinary":
        """Replies for one command, used in order; the last one repeats.
        Each is (output, exit code), where a non-str output is dumped as JSON,
        or a dict with out/code/sleep."""
        out = []
        for r in replies:
            if isinstance(r, dict):
                out.append(r)
                continue
            body, code = r
            out.append({"out": body if isinstance(body, str) else json.dumps(body, sort_keys=True),
                        "code": code})
        self.config[command] = out
        self._cfg.write_text(json.dumps(self.config))
        return self

    def calls(self, command: str | None = None) -> list[dict]:
        if not self._calls.exists():
            return []
        rows = [json.loads(line) for line in self._calls.read_text().splitlines()]
        rows = [{**r, "stdin_json": _maybe_json(r["stdin"])} for r in rows]
        return [r for r in rows if command is None or r["cmd"] == command]


def _maybe_json(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return None
