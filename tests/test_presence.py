"""presence.py owns the fixed paths and the words (consumer, action, method).
Slice 3 of the presence proof (#392)."""
import pytest

from scar import presence


def test_the_fixed_paths_are_absolute_administrator_paths():
    assert presence.SIGNER == "/Library/Application Support/handsal/bin/handsal-sign"
    assert presence.CHECKER == "/Library/Application Support/handsal/bin/handsal-check"


def test_the_consumer_id_is_pinned_because_renaming_breaks_every_stored_act():
    # Signed domain separation: a rename makes every act already stored
    # consumer_mismatch on recheck-act. This value must never change.
    assert presence.CONSUMER == "scar"


def test_the_words_of_a_promotion_decision():
    assert presence.ACTION == "promote"
    assert presence.METHOD == "local:handsal.decision"


def test_the_validity_window_is_a_decimal_string_constant():
    # SG-04: expires_in_s is a decimal string, never a JSON number, and Scar
    # keeps no copy of the org's policy.
    assert presence.EXPIRES_IN_S == "3600"
    assert isinstance(presence.EXPIRES_IN_S, str)



def _jws(payload_bytes):
    import base64
    return "h." + base64.urlsafe_b64encode(payload_bytes).decode().rstrip("=") + ".s"


def test_act_inputs_hash_reads_the_payload_without_verifying_anything():
    assert presence.act_inputs_hash(_jws(b'{"inputs_hash": "uEiabc", "x": 1}')) == "uEiabc"


@pytest.mark.parametrize("bad", [
    "", "a.b", "a.b.c.d", "h.!!.s", _jws(b"nope"), _jws(b"[]"), _jws(b"{}"),
    _jws(b'{"inputs_hash": 5}'), _jws(b'{"inputs_hash": ""}'), _jws(b"\xff\xfe"),
])
def test_act_inputs_hash_raises_on_anything_malformed(bad):
    with pytest.raises(presence.MalformedAct):
        presence.act_inputs_hash(bad)


# ---- act_inputs_hash reads inputs_hash and nothing else (#439) ----------------
# Each decoy key holds a different value, so a function that preferred or fell
# back to a neighbouring key returns something other than the expected one.

import json as _json  # noqa: E402

DECOYS = {
    "input_hash": "uEiDecoyInput",
    "hash": "uEiDecoyHash",
    "record_hash": "uEiDecoyRecord",
    "decision_hash": "uEiDecoyDecision",
    "claims": {"inputs_hash": "uEiDecoyNested"},
}


def _payload_jws(payload: dict) -> str:
    return _jws(_json.dumps(payload).encode())


@pytest.mark.parametrize("decoys_first", [True, False])
def test_act_inputs_hash_ignores_decoy_keys(decoys_first):
    real = {"inputs_hash": "uEiREAL"}
    payload = {**DECOYS, **real} if decoys_first else {**real, **DECOYS}
    assert list(payload)[0] == ("input_hash" if decoys_first else "inputs_hash")
    assert presence.act_inputs_hash(_payload_jws(payload)) == "uEiREAL"


@pytest.mark.parametrize("payload", [
    DECOYS,
    {"claims": {"inputs_hash": "uEiNestedOnly"}},
    {"input_hash": "uEiX"},
    {"record_hash": "uEiX"},
    {"Inputs_Hash": "uEiX", "inputsHash": "uEiX"},
])
def test_act_inputs_hash_does_not_fall_back_to_a_decoy(payload):
    with pytest.raises(presence.MalformedAct):
        presence.act_inputs_hash(_payload_jws(payload))


def _scar_text(act_line=""):
    return ("---\nid: 1\ntype: fence\ntitle: T\nseverity: low\nconfidence: 0.5\n"
            "created: 2026-11-01\nauthors: [\"claude-code\"]\npromoted_by: kibukx\n"
            f"{act_line}anchors:\n  - path: src/x.py\nstatus: active\n---\n\nBody.\n")


def test_act_matches_record_is_false_when_only_a_decoy_matches():
    from scar.model import parse_scar_text
    from scar.record import record_hash
    from scar.store import act_matches_record
    mine = record_hash(parse_scar_text(_scar_text()))
    # The act names another record under inputs_hash, and this record's hash
    # under every plausible neighbouring key.
    payload = {"inputs_hash": "uEiSomeOtherRecord", "input_hash": mine, "hash": mine,
               "record_hash": mine, "decision_hash": mine, "claims": {"inputs_hash": mine}}
    scar = parse_scar_text(_scar_text(f"promotion_act: {_payload_jws(payload)}\n"))
    assert act_matches_record(scar) is False
    honest = parse_scar_text(_scar_text(
        f"promotion_act: {_payload_jws({'inputs_hash': mine, **DECOYS})}\n"))
    assert act_matches_record(honest) is True
