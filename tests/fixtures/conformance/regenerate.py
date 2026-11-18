"""Regenerate promote.json, the static fixture of the conformance round trip.

    uv run python tests/fixtures/conformance/regenerate.py SIGNER_SRC

SIGNER_SRC is an unpacked copy of the pinned tag of the signer project (for
example `git archive <tag> | tar -x -C <empty dir>`). Only that project's
published test fixture keys are used: the act is signed by a fixture device of
a fixture org, never by a production key. The output is byte-identical on every
run (deterministic signatures and nonces), so a diff means an input changed.
Running this is part of a deliberate pin upgrade, never automatic.
"""
from __future__ import annotations

import base64
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))  # tests/, for conformance_support

import conformance_support as cs  # noqa: E402
from scar.model import parse_scar_text  # noqa: E402
from scar.presence import ACTION, CONSUMER, METHOD  # noqa: E402
from scar.record import promoted_record, promotion_subject  # noqa: E402


def _expires(jws: str) -> str:
    payload = jws.split(".")[1]
    raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    return json.loads(raw)["ext"]["dev.handsal/act"]["expires"]


def main(signer_src: str) -> None:
    gen = Path(signer_src) / "conformance" / "vectors" / "_gen"
    sys.path.insert(0, str(gen))
    from lib import base, inputs

    fx = base.Fixtures(base.fixture_profile())
    scar = parse_scar_text(cs.FIXTURE_SCAR)
    with tempfile.TemporaryDirectory() as tmp:
        repo = cs.make_repo(Path(tmp) / "repo")
        subject = promotion_subject(repo, scar.id)
    record = promoted_record(scar)
    jws = fx.decision(consumer=CONSUMER, action=ACTION, subject=subject, content=record)
    view = inputs._view(fx, False)
    check = inputs.check_input(
        fx, jws, method=inputs.DECISION, action=ACTION, consumer=CONSUMER,
        decision_b64=base64.b64encode(record).decode("ascii"), subject=subject)
    out = {
        "provenance": {
            "tag": cs.TAG, "commit": cs.COMMIT,
            "generator": "conformance/vectors/_gen/lib/base.py Fixtures.decision",
            "regenerate": "tests/fixtures/conformance/regenerate.py",
            "note": "fixture org and fixture device keys only; never a production key",
        },
        "org": view["org"], "subject": subject, "method": METHOD,
        "jws": jws, "now": view["now"], "expires": _expires(jws),
        "pin": view["pin"], "lineage": view["lineage"],
    }
    assert check["jws"] == jws and check["org"] == out["org"]
    FIXTURE = cs.FIXTURE_PATH
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
