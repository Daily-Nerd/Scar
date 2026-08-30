"""A dead posttool hook must not be readable as perfect compliance (#277).

The pre-#277 guard caught the precheck half dying (violations recorded with
zero precheck rows). It could not catch the mirror, because posttool wrote
nothing at all on a clean edit, so "no violation row" meant either compliance
or a dead hook and the two were byte-identical.
"""

from __future__ import annotations

import json

import pytest

from scar.match import armed_scar_ids, find_violations
from scar.store import ScarStore


def _scar(scar_id: int, violation: str | None = "forbidden_call") -> str:
    v = f'violation: "{violation}"\n' if violation else ""
    return (
        "---\n"
        f"id: {scar_id}\n"
        "type: landmine\n"
        f"title: scar {scar_id}\n"
        "severity: medium\n"
        "confidence: 0.7\n"
        "anchors:\n"
        "  - path: src/\n"
        f"{v}"
        "evidence:\n"
        "  - issue: 277\n"
        "status: active\n"
        "---\n\n"
        "Body.\n"
    )


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("clean\n")
    scars = tmp_path / ".scars"
    scars.mkdir()
    (scars / "0001-armed.landmine.md").write_text(_scar(1))
    (scars / "0002-unarmed.landmine.md").write_text(_scar(2, violation=None))
    store = ScarStore.discover(tmp_path)
    assert store is not None
    return store


# --- the shared candidacy definition --------------------------------------

def test_armed_ids_names_scars_that_could_have_violated(repo):
    assert armed_scar_ids(repo, "src/a.py") == [1]


def test_unarmed_scar_is_never_expected_to_produce_a_verdict(repo):
    # A scar with no violation regex cannot ever be recorded as violated, so
    # no verdict is owed for it and its presence must not create an
    # expectation that can never resolve.
    assert 2 not in armed_scar_ids(repo, "src/a.py")


def test_unanchored_path_expects_nothing(repo):
    (repo.root / "other").mkdir()
    assert armed_scar_ids(repo, "other/b.py") == []


def test_candidacy_matches_the_violation_matcher(repo):
    """One definition, two consumers. If these drift, the tool expects a
    verdict for a scar the matcher would never have evaluated."""
    armed = armed_scar_ids(repo, "src/a.py")
    hit = [v.scar.id for v in find_violations(repo, "src/a.py", "forbidden_call()")]
    assert set(hit).issubset(set(armed))


def test_a_scar_does_not_expect_a_verdict_on_its_own_file(repo):
    # Per-file self-exclusion (#148): a scar body quotes its own forbidden
    # construct, so it is not evaluated there and must not be expected there.
    assert armed_scar_ids(repo, ".scars/0001-armed.landmine.md") == []


# --- posttool now leaves a trace when it runs clean -----------------------

def _run_posttool(monkeypatch, capsys, store, target, content, edit_id="e1"):
    from scar import hooks
    payload = {"tool_use_id": edit_id,
               "tool_input": {"file_path": str(target), "content": content}}
    monkeypatch.setattr(hooks, "_read_payload", lambda: payload)
    rc = hooks.posttool()
    capsys.readouterr()
    return rc


def _rows(state):
    path = state / "firing-log.jsonl"
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


@pytest.fixture
def state(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    monkeypatch.setenv("SCAR_STATE_DIR", str(d))
    return d


def test_clean_edit_on_armed_path_records_an_observed_verdict(repo, state, monkeypatch, capsys):
    target = repo.root / "src" / "a.py"
    _run_posttool(monkeypatch, capsys, repo, target, "totally fine\n")
    rows = _rows(state)
    assert len(rows) == 1
    assert rows[0]["verdict_observed"] is True
    assert rows[0]["violation_ids"] == []
    assert rows[0]["verdict_armed_ids"] == [1]


def test_violating_edit_still_records_the_violation(repo, state, monkeypatch, capsys):
    target = repo.root / "src" / "a.py"
    _run_posttool(monkeypatch, capsys, repo, target, "forbidden_call()\n")
    rows = _rows(state)
    assert rows[0]["violation_ids"] == [1]
    assert rows[0]["verdict_observed"] is True


def test_no_armed_scar_writes_nothing(repo, state, monkeypatch, capsys):
    """Volume guard. A violation was impossible here, so no verdict is owed
    and the log must not grow on every unrelated edit in the repo."""
    (repo.root / "other").mkdir()
    target = repo.root / "other" / "b.py"
    target.write_text("x\n")
    _run_posttool(monkeypatch, capsys, repo, target, "x\n")
    assert _rows(state) == []


def test_logging_never_breaks_the_hook(repo, state, monkeypatch, capsys):
    from scar import hooks
    monkeypatch.setattr(hooks, "firing_log_path",
                        lambda: (_ for _ in ()).throw(OSError("boom")))
    target = repo.root / "src" / "a.py"
    assert _run_posttool(monkeypatch, capsys, repo, target, "fine\n") == 0


# --- the aggregate: silence must not read as compliance -------------------

def _agg(records):
    from scar.cli import _aggregate_firings
    return _aggregate_firings(records)


def _precheck(edit_id, armed, sids=(1,)):
    """A post-upgrade precheck row: carries the expectation marker, so the
    reader knows the writer could also have resolved it."""
    from scar import hooks
    rec = {"repo": "/r", "ts": "2026-01-01T00:00:00", "scar_ids": list(sids),
           "count": len(sids), "armed_ids": list(armed),
           hooks.VERDICT_EXPECTED_KEY: bool(armed)}
    if edit_id:
        rec["edit_id"] = edit_id
    return rec


def _verdict(edit_id, violated=()):
    rec = {"repo": "/r", "ts": "2026-01-01T00:00:01",
           "violation_ids": list(violated), "count": len(violated),
           "verdict_observed": True, "verdict_armed_ids": [1]}
    if edit_id:
        rec["edit_id"] = edit_id
    return rec


def test_armed_firing_with_no_verdict_is_unresolved_not_compliant():
    agg = _agg([_precheck("e1", armed=[1])])
    assert agg["verdicts_expected"] == 1
    assert agg["verdicts_observed"] == 0
    assert agg["verdicts_unresolved"] == 1


def test_a_matching_verdict_resolves_it():
    agg = _agg([_precheck("e1", armed=[1]), _verdict("e1")])
    assert agg["verdicts_unresolved"] == 0
    assert agg["verdicts_observed"] == 1


def test_posttool_silent_when_armed_firings_have_no_verdicts_at_all():
    agg = _agg([_precheck("e1", armed=[1]), _precheck("e2", armed=[1])])
    assert agg["posttool_silent"] is True


def test_not_silent_once_any_verdict_is_observed():
    """One-directional: this can prove the half is dead, never prove it live.
    A single observed verdict clears the alarm and claims nothing more."""
    agg = _agg([_precheck("e1", armed=[1]), _precheck("e2", armed=[1]),
                _verdict("e1")])
    assert agg["posttool_silent"] is False


def test_unarmed_firing_expects_no_verdict():
    # A firing on a scar with no tripwire could never be recorded as violated,
    # so it owes no verdict and must not create a permanent unresolved row.
    agg = _agg([_precheck("e1", armed=[])])
    assert agg["verdicts_expected"] == 0
    assert agg["posttool_silent"] is False


def test_rows_predating_the_field_are_unplaceable_not_unresolved():
    """#266 again: a MISSING armed_ids key means the row predates the field.
    Counting those as unresolved would make every historical window look
    broken forever, which is the mirror error of counting them as clean."""
    old = {"repo": "/r", "ts": "2026-01-01T00:00:00", "scar_ids": [1],
           "count": 1, "edit_id": "e1"}
    agg = _agg([old])
    assert agg["verdicts_expected"] == 0
    assert agg["verdicts_unplaceable"] == 1
    assert agg["posttool_silent"] is False


def test_armed_firing_without_an_edit_id_cannot_be_joined():
    # No correlation key, so it can be neither resolved nor called unresolved.
    agg = _agg([_precheck(None, armed=[1])])
    assert agg["verdicts_unresolved"] == 0
    assert agg["verdicts_unplaceable"] == 1


def test_health_line_names_the_silent_posttool_half():
    from scar.cli import _health_lines
    agg = _agg([_precheck("e1", armed=[1])])
    from scar.cli import _health_block
    lines = _health_lines(_health_block(agg))
    assert any("posttool" in ln.lower() for ln in lines)


def test_health_is_quiet_when_verdicts_are_observed():
    from scar.cli import _health_block, _health_lines
    agg = _agg([_precheck("e1", armed=[1]), _verdict("e1")])
    lines = _health_lines(_health_block(agg))
    assert not any("posttool" in ln.lower() for ln in lines)


# --- the upgrade case: old rows must not cry wolf -------------------------

def test_armed_rows_from_before_the_mechanism_are_unplaceable():
    """The regression the dogfood caught. armed_ids says a verdict was OWED.
    It does not say the writer could RESOLVE it. Counting a pre-upgrade armed
    firing as unresolved makes every existing install show a broken-install
    warning on upgrade, before its next armed edit."""
    pre_upgrade = {"repo": "/r", "ts": "2026-01-01T00:00:00", "scar_ids": [1],
                   "count": 1, "armed_ids": [1], "edit_id": "old1"}
    agg = _agg([pre_upgrade])
    assert agg["verdicts_expected"] == 0
    assert agg["verdicts_unresolved"] == 0
    assert agg["verdicts_unplaceable"] == 1
    assert agg["posttool_silent"] is False


def test_post_upgrade_row_carries_the_expectation_marker():
    from scar import hooks
    rec = {"repo": "/r", "ts": "2026-01-01T00:00:00", "scar_ids": [1],
           "count": 1, "armed_ids": [1], "edit_id": "e1",
           hooks.VERDICT_EXPECTED_KEY: True}
    agg = _agg([rec])
    assert agg["verdicts_expected"] == 1
    assert agg["verdicts_unresolved"] == 1
    assert agg["posttool_silent"] is True


def test_precheck_writes_the_expectation_marker_for_armed_firings(tmp_path, monkeypatch):
    from scar import hooks
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "a.py"
    target.write_text("x\n")
    scars = tmp_path / ".scars"
    scars.mkdir()
    (scars / "0001-armed.landmine.md").write_text(_scar(1))
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    monkeypatch.setattr(hooks, "_read_payload", lambda: {
        "tool_use_id": "e1",
        "tool_input": {"file_path": str(target), "content": "x\n"}})
    hooks.precheck()
    rows = _rows(state)
    assert rows and rows[0][hooks.VERDICT_EXPECTED_KEY] is True


def test_unarmed_firing_carries_no_expectation_marker(tmp_path, monkeypatch):
    from scar import hooks
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "a.py"
    target.write_text("x\n")
    scars = tmp_path / ".scars"
    scars.mkdir()
    (scars / "0002-unarmed.landmine.md").write_text(_scar(2, violation=None))
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    monkeypatch.setattr(hooks, "_read_payload", lambda: {
        "tool_use_id": "e1",
        "tool_input": {"file_path": str(target), "content": "x\n"}})
    hooks.precheck()
    rows = _rows(state)
    assert rows and rows[0].get(hooks.VERDICT_EXPECTED_KEY) is False


# --- the documented keys must actually be emitted -------------------------

def test_every_documented_verdict_key_reaches_stats_json(tmp_path, monkeypatch, capsys):
    """SPEC 9.3 lists these as guaranteed top-level keys. The dogfood found
    three of them missing from the payload because only HEALTH_KEYS survived
    into the output dict."""
    from scar import cli
    (tmp_path / ".scars").mkdir()
    (tmp_path / ".scars" / "0001-a.landmine.md").write_text(_scar(1))
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("SCAR_STATE_DIR", str(state))
    monkeypatch.chdir(tmp_path)
    assert cli.main(["stats", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    for key in ("verdicts_expected", "verdicts_observed", "verdicts_unresolved",
                "verdicts_unplaceable", "posttool_silent"):
        assert key in data, f"{key} documented in SPEC 9.3 but absent from --json"
        assert data[key] is not None


def test_the_expectation_key_constant_does_not_drift():
    """cli duplicates the key name to keep a hook import off the read path.
    Two spellings of one key is how the writer and the reader stop agreeing."""
    from scar import cli, hooks
    assert cli._VERDICT_EXPECTED_KEY == hooks.VERDICT_EXPECTED_KEY
