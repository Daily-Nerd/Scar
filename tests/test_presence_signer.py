"""The signer adapter and the checker's enrolled call, against fakes only (#392).
Every signer exit code and refusal the profile defines is covered here."""
import base64
import json

import pytest
from presence_fakes import FakeBinary

from scar import presence

ORG = "uEiARDVf_JDy-0rOjSM1lEH6YMSEdD2RQiuviL-AsSVLq4A"
HEAD = {"id": "uEiBheadheadheadheadheadheadheadheadheadheadhead", "kind": "chain"}
RECORD = b"id: 4\ntype: fence\n---\nBody.\n"
SUBJECT = "4-aabbccddeeff0011"


@pytest.fixture
def signer(tmp_path, monkeypatch):
    fake = FakeBinary(tmp_path, "handsal-sign")
    monkeypatch.setattr(presence, "SIGNER", str(fake.path))
    return fake


@pytest.fixture
def checker(tmp_path, monkeypatch):
    fake = FakeBinary(tmp_path, "handsal-check")
    monkeypatch.setattr(presence, "CHECKER", str(fake.path))
    return fake


def test_decision_input_is_exactly_the_draft_the_profile_defines():
    got = presence.decision_input(ORG, SUBJECT, RECORD, HEAD)
    assert got == {
        "attachments": {"decision_b64": base64.b64encode(RECORD).decode("ascii")},
        "draft": {
            "body": {"action": "promote", "consumer": "scar", "subject": SUBJECT},
            "expires_in_s": "3600",
            "head": HEAD,
            "method": "local:handsal.decision",
            "org": ORG,
        },
    }


def test_the_validity_window_is_a_string_never_a_number():
    got = presence.decision_input(ORG, SUBJECT, RECORD, HEAD)
    assert got["draft"]["expires_in_s"] == presence.EXPIRES_IN_S
    assert isinstance(got["draft"]["expires_in_s"], str)


def test_the_head_is_copied_not_rebuilt():
    got = presence.decision_input(ORG, SUBJECT, RECORD, {"id": "h", "kind": "chain"})
    assert got["draft"]["head"] == {"id": "h", "kind": "chain"}


def test_the_signer_stdin_is_in_the_canonical_subset(signer):
    # No whitespace outside strings, no numbers, no booleans (ACT-04).
    signer.reply("render", ({"first_line": "l", "text": "t"}, 0))
    presence.render(presence.decision_input(ORG, SUBJECT, RECORD, HEAD))
    raw = signer.calls("render")[0]["stdin"].rstrip("\n")
    assert " " not in raw.replace(SUBJECT, "").replace("local:handsal.decision", "")
    assert "\n" not in raw
    parsed = json.loads(raw)
    assert json.dumps(parsed, sort_keys=True, separators=(",", ":")) == raw

    def no_non_string_scalars(v):
        if isinstance(v, dict):
            return all(no_non_string_scalars(x) for x in v.values())
        if isinstance(v, list):
            return all(no_non_string_scalars(x) for x in v)
        return isinstance(v, str)
    assert no_non_string_scalars(parsed)


def test_render_returns_the_signer_words_verbatim(signer):
    signer.reply("render", ({"first_line": "line one", "text": "line one\nline two\n"}, 0))
    out = presence.render(presence.decision_input(ORG, SUBJECT, RECORD, HEAD))
    assert out == {"first_line": "line one", "text": "line one\nline two\n"}
    assert signer.calls("render")[0]["argv"] == ["render"]


def test_a_refused_render_is_refused_with_its_code(signer):
    signer.reply("render", ({"error": "render_refused"}, 2))
    with pytest.raises(presence.Refused) as exc:
        presence.render(presence.decision_input(ORG, SUBJECT, RECORD, HEAD))
    assert exc.value.reason == "render_refused"


GOOD = {"jws": "aaa.bbb.ccc", "receipt_id": "uEiReceipt"}


def test_sign_returns_the_jws_and_the_receipt(signer):
    signer.reply("sign", (GOOD, 0))
    assert presence.sign(presence.decision_input(ORG, SUBJECT, RECORD, HEAD)) == (
        "aaa.bbb.ccc", "uEiReceipt")
    assert signer.calls("sign")[0]["argv"] == ["sign"]


def test_sign_exit_1_is_a_cancel(signer):
    signer.reply("sign", ({"error": "user_cancel"}, 1))
    with pytest.raises(presence.Cancelled):
        presence.sign(presence.decision_input(ORG, SUBJECT, RECORD, HEAD))


@pytest.mark.parametrize("code,reason", [
    (2, "schema_refused"), (2, "render_refused"), (2, "attachment_mismatch"),
    (2, "identity_mismatch"), (3, "key_unavailable"), (4, "no_display"),
    (4, "prompt_refused"), (4, "biometric_unavailable"), (5, "internal"),
])
def test_every_other_signer_exit_is_a_refusal_with_its_code(signer, code, reason):
    signer.reply("sign", ({"error": reason}, code))
    with pytest.raises(presence.Refused) as exc:
        presence.sign(presence.decision_input(ORG, SUBJECT, RECORD, HEAD))
    assert exc.value.reason == reason


@pytest.mark.parametrize("reply,code", [
    ("not json", 0),
    ('{"jws":"a.b.c"}', 0),
    ('{"receipt_id":"r"}', 0),
    ('{"error":"internal"}', 0),
    ('{"jws":"","receipt_id":"r"}', 0),
    ('{"jws":5,"receipt_id":"r"}', 0),
    ("not json", 2),
    ("", 7),
])
def test_a_signature_is_only_ever_a_clean_jws_and_receipt(signer, reply, code):
    signer.reply("sign", (reply, code))
    with pytest.raises(presence.PresenceError):
        presence.sign(presence.decision_input(ORG, SUBJECT, RECORD, HEAD))


def test_a_missing_signer_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(presence, "SIGNER", str(tmp_path / "nope"))
    with pytest.raises(presence.Unavailable):
        presence.sign(presence.decision_input(ORG, SUBJECT, RECORD, HEAD))


def test_a_signer_that_never_answers_times_out(signer, monkeypatch):
    signer.reply("sign", {"out": "{}", "code": 0, "sleep": 3})
    monkeypatch.setattr(presence, "SIGNER_TIMEOUT_S", 0.5)
    with pytest.raises(presence.Unavailable):
        presence.sign(presence.decision_input(ORG, SUBJECT, RECORD, HEAD))


def test_local_kid_reads_the_public_key_id_without_a_prompt_command(signer):
    signer.reply("pubkey", ({"agree": {"x": "a", "y": "b"}, "class": "apple-se-bio",
                             "kid": "KID43", "sign": {"x": "c", "y": "d"}}, 0))
    assert presence.local_kid() == "KID43"
    assert signer.calls("pubkey")[0]["argv"] == ["pubkey"]
    assert signer.calls("pubkey")[0]["stdin"] == ""


def test_local_kid_with_no_key_is_a_refusal(signer):
    signer.reply("pubkey", ({"error": "key_unavailable"}, 3))
    with pytest.raises(presence.Refused) as exc:
        presence.local_kid()
    assert exc.value.reason == "key_unavailable"


def test_enrolled_true_passes_and_sends_the_kid_and_org(checker):
    checker.reply("enrolled", ({"enrolled": True, "reason": "ok"}, 0))
    presence.enrolled(ORG, "KID43")
    assert checker.calls("enrolled")[0]["stdin_json"] == {"device_kid": "KID43", "org": ORG}


@pytest.mark.parametrize("reason", ["local_key_not_active", "local_key_unknown",
                                    "stale_view", "pin_missing", "fixture_profile"])
def test_enrolled_false_carries_the_reason_verbatim(checker, reason):
    checker.reply("enrolled", ({"enrolled": False, "reason": reason}, 0))
    with pytest.raises(presence.Refused) as exc:
        presence.enrolled(ORG, "KID43")
    assert exc.value.reason == reason


@pytest.mark.parametrize("reply", ['{"enrolled":"yes","reason":"ok"}', '{"reason":"ok"}', "nope"])
def test_enrolled_needs_a_real_true(checker, reply):
    checker.reply("enrolled", (reply, 0))
    with pytest.raises(presence.PresenceError):
        presence.enrolled(ORG, "KID43")


# ---- first-acceptance check -------------------------------------------------

def test_ingest_sends_exactly_the_check_act_input_and_returns_the_receipt(checker):
    checker.reply("check-act", ({"class": "apple-se-bio", "reason": "ok",
                                 "receipt_id": "uEiReceipt", "valid": True}, 0))
    got = presence.ingest(ORG, SUBJECT, RECORD, "aaa.bbb.ccc", ["uEiOld1", "uEiOld2"])
    assert got == "uEiReceipt"
    (call,) = checker.calls("check-act")
    assert call["argv"] == ["check-act"]
    assert call["stdin_json"] == {
        "action": "promote",
        "consumer": "scar",
        "decision_b64": base64.b64encode(RECORD).decode("ascii"),
        "expect_method": "local:handsal.decision",
        "jws": "aaa.bbb.ccc",
        "org": ORG,
        "seen": ["uEiOld1", "uEiOld2"],
        "subject": SUBJECT,
    }


@pytest.mark.parametrize("reason", [
    "duplicate_receipt", "expired", "revoked_key", "validity_too_long",
    "inputs_mismatch", "consumer_mismatch", "stale_view", "weird_new_code",
])
def test_a_refusal_after_signing_carries_the_checker_reason_verbatim(checker, reason):
    checker.reply("check-act", ({"class": None, "reason": reason,
                                 "receipt_id": "uEiR", "valid": False}, 0))
    with pytest.raises(presence.Refused) as exc:
        presence.ingest(ORG, SUBJECT, RECORD, "a.b.c", [])
    assert exc.value.reason == reason


@pytest.mark.parametrize("reply,code", [
    ("not json", 0),
    ('{"error":"malformed_input"}', 0),
    ('{"class":"x","reason":"ok","receipt_id":"r","valid":true}', 1),
    ('{"class":"x","reason":"ok","receipt_id":"r","valid":"true"}', 0),
    ('{"class":"x","reason":"ok","receipt_id":"","valid":true}', 0),
    ('{"class":"x","reason":"ok","receipt_id":null,"valid":true}', 0),
    ('{"class":"x","reason":"something","receipt_id":"r","valid":true}', 0),
    ('{"reason":"ok","valid":true}', 0),
])
def test_only_valid_true_with_ok_and_a_receipt_is_accepted(checker, reply, code):
    checker.reply("check-act", (reply, code))
    with pytest.raises(presence.PresenceError):
        presence.ingest(ORG, SUBJECT, RECORD, "a.b.c", [])
