"""The signed promotion against the real external checker (#397).

Every other presence test runs against a fake program, so none of them can see
the checker change what it accepts. These drive Scar's own functions
(presence.ingest and the recheck in verify) through a test-only shim over the
pinned conformance checker and verifier, with one fixed act, so the request
Scar really builds is what gets judged.

Locally the round trip skips when the tools are absent. In CI the job sets
CONFORMANCE_REQUIRED=1 and a missing or wrong tool is a failure: a skipped
round trip proves nothing and must not look like a pass.
"""
from __future__ import annotations

import json
import os

import pytest

import conformance_support as cs
from scar import presence, verify
from scar.model import parse_scar_text
from scar.record import promoted_record, promotion_subject

# ---- the gate: tests that always run ----------------------------------------


def test_required_only_when_ci_says_so():
    assert cs.required({cs.ENV_REQUIRED: "1"}) is True
    assert cs.required({}) is False
    assert cs.required({cs.ENV_REQUIRED: "0"}) is False


def test_absent_tools_are_reported_as_absent():
    with pytest.raises(cs.ToolsUnusable) as exc:
        cs.resolve_tools({})
    assert exc.value.absent is True


def test_a_named_file_that_is_not_the_pin_is_wrong_not_absent(tmp_path):
    fake = tmp_path / "verifier"
    fake.write_text("always says yes")
    src = tmp_path / "src"
    (src / cs.CHECKER_PACKAGE).mkdir(parents=True)
    with pytest.raises(cs.ToolsUnusable) as exc:
        cs.resolve_tools({cs.ENV_VERIFIER: str(fake), cs.ENV_CHECKER_SRC: str(src)})
    assert exc.value.absent is False


def test_the_checker_source_is_pinned_by_content(tmp_path):
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "a.py").write_text("x = 1\n")
    before = cs.tree_digest(package)
    (package / "a.py").write_text("x = 2\n")
    assert cs.tree_digest(package) != before
    (package / "__pycache__").mkdir()
    (package / "__pycache__" / "a.pyc").write_bytes(b"noise")
    assert cs.tree_digest(package) != before  # still the edited content
    (package / "a.py").write_text("x = 1\n")
    assert cs.tree_digest(package) == before  # bytecode never counts


def test_the_fixture_root_commit_is_deterministic(tmp_path):
    fixture = json.loads(cs.FIXTURE_PATH.read_text())
    repo = cs.make_repo(tmp_path / "r")
    scar = parse_scar_text(cs.FIXTURE_SCAR)
    assert promotion_subject(repo, scar.id) == fixture["subject"]


# ---- the round trip ----------------------------------------------------------


@pytest.fixture(scope="module")
def tools():
    try:
        return cs.resolve_tools(os.environ)
    except cs.ToolsUnusable as exc:
        if cs.required(os.environ) or not exc.absent:
            pytest.fail(f"conformance tools unusable: {exc}")
        pytest.skip(f"conformance tools not available: {exc}")


@pytest.fixture
def act(tools, tmp_path, monkeypatch):
    verifier, src = tools
    fixture = json.loads(cs.FIXTURE_PATH.read_text())
    shim = cs.Shim(tmp_path, verifier, src, fixture)
    monkeypatch.setattr(presence, "CHECKER", str(shim.path))
    repo = cs.make_repo(tmp_path / "repo")
    scar = parse_scar_text(cs.FIXTURE_SCAR)
    scar.promotion_act = fixture["jws"]

    class Act:
        pass

    a = Act()
    a.fixture, a.shim, a.repo, a.scar = fixture, shim, repo, scar
    a.record = promoted_record(scar)
    return a


def ingest(a, **over):
    args = {"org": a.fixture["org"], "subject": a.fixture["subject"], "record": a.record,
            "jws": a.fixture["jws"], "seen": []}
    args.update(over)
    return presence.ingest(args["org"], args["subject"], args["record"], args["jws"], args["seen"])


def recheck(a, **over):
    kw = {"org": a.fixture["org"], "repo": a.repo}
    kw.update(over)
    return verify.verify_scar(a.scar, **kw)


def test_check_act_accepts_the_act_scar_builds(act):
    assert ingest(act)  # a receipt id


def test_recheck_act_accepts_it(act):
    assert recheck(act) == verify.VERIFIED


def test_an_edited_record_is_inputs_mismatch(act):
    with pytest.raises(presence.Refused) as exc:
        ingest(act, record=act.record + b"edited\n")
    assert exc.value.reason == "inputs_mismatch"
    act.scar.title = "Keep the odd retry loop, edited"
    assert recheck(act) == "not proven: inputs_mismatch"


def test_another_repository_root_is_outputs_mismatch(act, tmp_path):
    other = cs.make_repo(tmp_path / "other", message="a different root")
    other_subject = promotion_subject(other, act.scar.id)
    assert other_subject != act.fixture["subject"]
    with pytest.raises(presence.Refused) as exc:
        ingest(act, subject=other_subject)
    assert exc.value.reason == "outputs_mismatch"
    assert recheck(act, repo=other) == "not proven: outputs_mismatch"


def test_a_receipt_already_seen_is_duplicate_receipt(act):
    receipt = ingest(act)
    with pytest.raises(presence.Refused) as exc:
        ingest(act, seen=[receipt])
    assert exc.value.reason == "duplicate_receipt"


def test_an_expired_act_is_refused_at_acceptance_and_still_rechecks(act):
    act.shim.set_clock("2026-11-08T14:00:00Z")
    assert act.fixture["expires"] < "2026-11-08T14:00:00Z"
    with pytest.raises(presence.Refused) as exc:
        ingest(act)
    assert exc.value.reason == "expired"
    assert recheck(act) == verify.VERIFIED


def test_a_request_missing_a_key_is_refused_by_the_real_checker(act):
    # The red case: a wrong shape must fail here, or the checker is not being asked.
    with pytest.raises(presence.Unavailable, match="malformed_input"):
        presence._checker("check-act", {"action": presence.ACTION, "org": act.fixture["org"]})


def test_the_shim_never_overrides_what_scar_sends(act):
    with pytest.raises(presence.Unavailable, match="shim_clash"):
        presence._checker("check-act", {"now": "2026-11-08T12:00:00Z"})
