"""The `--json` stability contract (SPEC §9).

These payloads are consumed from OUTSIDE this repo — an agent runtime, or a
sibling project probing `scar` as an optional external binary. SPEC §9 promises
a set of keys per subcommand; this file is the gate that makes the promise
enforceable rather than aspirational.

Two deliberate design choices:

1. Every key assertion is a SUBSET check, never equality. SPEC §9.1 says new
   keys may appear in any release and consumers must ignore unknown ones.
   Asserting an exact key set would make every additive change look breaking,
   which would train people to edit this file instead of reading it.

2. `test_spec_documents_every_json_subcommand` compares the SPEC table against
   the actual argparse surface. Adding `--json` to a tenth command without
   documenting it is the realistic drift, and nothing else in the suite
   catches it.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from scar.cli import main
from scar.store import init_scars

ROOT = Path(__file__).resolve().parent.parent

# SPEC §9.3. Keys promised on every successful --json run of each command.
GUARANTEED: dict[str, set[str]] = {
    "lint": {"files", "findings", "failed", "orphans", "partial_rot",
             "symbol_drift", "revivals", "reverse_hints",
             "unreachable_evidence", "shallow_clone"},
    "status": {"scars_dir", "active", "challenged", "candidates", "review_due",
               "orphan_detected", "orphaned", "partial_rot", "broken", "counts"},
    "check": {"paths", "scars"},
    "why": {"path", "records"},
    "stats": {"repo", "total_firings", "per_scar", "most_fired", "last_fired",
              "never_fired", "demotions", "edits_observed", "injection_rate",
              "retrieval_misses", "instrument_disconnected",
              "last_fired_age_days", "advisories"},
    "gc": {"removed_markers", "dropped_firings", "dry_run", "candidates",
           "fp_log"},
    "orphan": {"orphan_detected", "partial_rot"},
    "reanchor": {"proposals", "pattern_diagnostics"},
    # `message` is deliberately NOT here: it is added only on triggered: true
    # (see test_draft_check_message_is_conditional_on_triggered).
    "draft-check": {"triggered", "revert_language", "revert_commits",
                    "reset_hard", "churn"},
}

# Argv that reaches each command's --json path. `gc` is the only mutating one
# in the set, so the contract's observation mode is --dry-run (SPEC §9.3).
ARGV: dict[str, list[str]] = {
    "lint": ["lint", "--json"],
    "status": ["status", "--json"],
    "check": ["check", "src/", "--json"],
    "why": ["why", "src/", "--json"],
    "stats": ["stats", "--json"],
    "gc": ["gc", "--dry-run", "--json"],
    "orphan": ["orphan", "--json"],
    "reanchor": ["reanchor", "--json"],
    "draft-check": ["draft-check", "--json"],
}

# SPEC §9.3, right-hand column: keys promised on each object inside a list.
ITEM_KEYS: dict[tuple[str, str], set[str]] = {
    ("lint", "findings"): {"file", "level", "message"},
    ("status", "active"): {"id", "type", "severity", "title"},
    ("check", "scars"): {"id", "type", "severity", "confidence", "status",
                         "title", "body"},
    ("why", "records"): {"id", "type", "status", "title", "file", "body"},
    ("stats", "per_scar"): {"id", "count", "violations"},
    ("gc", "candidates"): {"name", "age_days"},
}

NESTED_OBJECTS: dict[tuple[str, str], set[str]] = {
    ("status", "counts"): {"active", "candidates", "orphan_detected",
                           "orphaned", "partial_rot", "broken"},
    ("gc", "fp_log"): {"present", "size", "lines"},
}


def _active_scar(id: int, title: str) -> str:
    return (
        f"---\nid: {id}\ntype: deadend\ntitle: {title}\nseverity: medium\n"
        f"confidence: 0.7\ncreated: 2026-06-10\nauthors: [k]\nanchors:\n"
        f"  - path: src/\nevidence:\n  - commit: abc1234\nstatus: active\n---\n\nBody.\n")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo populated so that EVERY array carrying promised item keys is
    non-empty.

    This matters more than it looks. An item-key assertion that skips on an
    empty list is a contract clause nothing enforces, so the fixture plants a
    lint finding, a stale candidate and firing-log rows on purpose.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    init_scars(tmp_path)

    (tmp_path / ".scars" / "0001-a.deadend.md").write_text(
        _active_scar(1, "Scar one"), encoding="utf-8")
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "thing.py").write_text("x = 1\n", encoding="utf-8")

    # lint.findings[] — a candidate with no YAML frontmatter is exactly the
    # failure the store cannot parse, so lint has something to report.
    candidates = tmp_path / ".scars" / "candidates"
    candidates.mkdir(parents=True, exist_ok=True)
    (candidates / "broken.md").write_text(
        "no frontmatter here, just prose\n", encoding="utf-8")

    # stats.per_scar[] — firing rows for the scar planted above.
    state = tmp_path / "state"
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    state.mkdir(parents=True, exist_ok=True)
    with (state / "firing-log.jsonl").open("w", encoding="utf-8") as fh:
        for ts in ("2026-06-10T10:00:00", "2026-06-11T09:00:00"):
            fh.write(json.dumps({"ts": ts, "repo": str(tmp_path),
                                 "target": "src/thing.py", "scar_ids": [1],
                                 "count": 1}) + "\n")

    # gc.candidates[] — gc REPORTS on repo-side candidates, never writes them.
    return tmp_path


def _payload(command: str, capsys) -> dict:
    assert main(ARGV[command]) in (0, 1), f"{command} --json did not complete"
    out = capsys.readouterr().out
    return json.loads(out)


@pytest.mark.parametrize("command", sorted(GUARANTEED))
def test_guaranteed_top_level_keys_are_present(command, repo, capsys):
    """SPEC §9.3. Subset, not equality — extra keys are additive and allowed."""
    data = _payload(command, capsys)
    missing = GUARANTEED[command] - set(data)
    assert not missing, f"scar {command} --json dropped promised keys: {sorted(missing)}"


@pytest.mark.parametrize("command,key", sorted(ITEM_KEYS))
def test_guaranteed_list_item_keys_are_present(command, key, repo, capsys):
    data = _payload(command, capsys)
    items = data.get(key) or []
    if not items:
        pytest.skip(f"{command}.{key} is empty in this fixture")
    missing = ITEM_KEYS[(command, key)] - set(items[0])
    assert not missing, f"{command}.{key}[] dropped promised keys: {sorted(missing)}"


@pytest.mark.parametrize("command,key", sorted(NESTED_OBJECTS))
def test_guaranteed_nested_object_keys_are_present(command, key, repo, capsys):
    data = _payload(command, capsys)
    missing = NESTED_OBJECTS[(command, key)] - set(data[key])
    assert not missing, f"{command}.{key} dropped promised keys: {sorted(missing)}"


# --- §9.2: null means refused, not zero -----------------------------------

def test_injection_rate_is_null_not_zero_without_zero_hit_logging(repo, capsys):
    """SPEC §9.2. Without SCAR_LOG_ZERO_HITS the log holds only edits that
    matched, so a ratio would be 100% by construction (#217). The contract is
    that the field is None — a consumer coercing it to 0 (or reading a
    flattering 1.0) publishes a number the data cannot support.
    """
    data = _payload("stats", capsys)
    assert data["injection_rate"] is None


def test_retrieval_misses_is_nullable_and_a_floor_not_a_rate(repo, capsys):
    """SPEC §9.2. `retrieval_misses` is either an int floor or None; it is
    never a percentage, and None is a refusal rather than a zero.
    """
    data = _payload("stats", capsys)
    assert data["retrieval_misses"] is None or isinstance(
        data["retrieval_misses"], int)
    assert not isinstance(data["retrieval_misses"], float)


def test_instrument_disconnected_false_does_not_imply_measurable(repo, capsys):
    """SPEC §9.2. `false` reports 'no observed impossibility', never a health
    certificate — and specifically it does NOT license reading the metrics.

    Pinned as the coexistence: the flag is `false` here while
    `injection_rate` is still refused. A consumer that gates on
    `not instrument_disconnected` and then trusts the rate would read a
    number the data cannot support.
    """
    data = _payload("stats", capsys)
    assert data["instrument_disconnected"] is False
    assert data["injection_rate"] is None


def test_draft_check_message_is_conditional_on_triggered(repo, capsys):
    """SPEC §9.3. `message` exists only when there is a verdict to carry.

    Written after the first draft of this contract promised `message`
    unconditionally — it was read off a live run that happened to be
    triggered, and a fresh repo is not. Pinned so the conditionality is a
    stated part of the contract rather than a surprise at the consumer.
    """
    data = _payload("draft-check", capsys)
    assert isinstance(data["triggered"], bool)
    if data["triggered"]:
        assert isinstance(data["message"], str)
    else:
        assert "message" not in data


def test_stats_window_key_is_conditional_on_the_window_flags(repo, capsys):
    """SPEC §9.3. `window` appears only when --since/--until is given.

    Same shape as draft-check.message: a key that exists only when there is
    something for it to say. Pinned so it is never promoted to unconditional,
    which would make an unwindowed run look windowed.
    """
    assert "window" not in _payload("stats", capsys)
    assert main(["stats", "--since", "2020-01-01", "--json"]) == 0
    windowed = json.loads(capsys.readouterr().out)
    assert set(windowed["window"]) >= {"since", "until", "excluded_undated"}


def test_gc_dry_run_echoes_the_mode_it_ran_in(repo, capsys):
    """SPEC §9.3. `gc` mutates; an observer passes --dry-run and needs the
    payload to confirm which mode actually ran.
    """
    data = _payload("gc", capsys)
    assert data["dry_run"] is True


# --- drift gate ------------------------------------------------------------

def _json_subcommands_from_cli() -> set[str]:
    """Top-level subcommands whose parser declares --json."""
    src = (ROOT / "src" / "scar" / "cli.py").read_text(encoding="utf-8")
    hits = [(m.start(), m.group(1))
            for m in re.finditer(r'_add\(\s*sub,\s*"([a-z][a-z-]*)"', src)]
    found = set()
    for i, (pos, name) in enumerate(hits):
        end = hits[i + 1][0] if i + 1 < len(hits) else len(src)
        if '"--json"' in src[pos:end]:
            found.add(name)
    return found


def _json_subcommands_from_spec() -> set[str]:
    """Command names in the SPEC §9.3 guaranteed-keys table."""
    spec = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    section = spec.split("### 9.3 Guaranteed keys", 1)[-1].split("\n### ", 1)[0]
    return set(re.findall(r"^\|\s*`([a-z][a-z-]*)`\s*\|", section, re.MULTILINE))


def test_spec_documents_every_json_subcommand():
    """A tenth `--json` command added without a SPEC §9.3 row is the realistic
    drift, and it silently ships an unpromised surface that consumers will
    nonetheless parse. Fail loudly instead.
    """
    assert _json_subcommands_from_cli() == _json_subcommands_from_spec()


def test_contract_table_matches_the_documented_commands():
    """This file and SPEC §9.3 must describe the same set of commands."""
    assert set(GUARANTEED) == _json_subcommands_from_spec()
