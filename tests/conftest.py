"""Suite-wide isolation.

The firing log is machine-GLOBAL: `firing_log_path()` resolves under the
user's real state dir unless SCAR_STATE_DIR says otherwise. A test that
touches it without redirecting that variable writes into the same log the
project publishes measurements from.

That is not hypothetical — 92 of 105 records were once pytest artifacts
(documented in website/docs/methodology.md). The fix at the time was a
per-test fixture, which leaves the class open: the next test that forgets
the line reintroduces it. This makes the guard structural (#228).
"""

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def _ambient_scar_variable():
    """Stand-in for a SCAR_* variable exported in a developer's shell.

    The per-test fixture below must strip it. Without a real one to strip,
    the guard test would pass trivially in CI — which is exactly how #239
    stayed invisible until someone set SCAR_LOG_ZERO_HITS locally.
    """
    os.environ["SCAR_AMBIENT_SENTINEL"] = "1"
    yield
    os.environ.pop("SCAR_AMBIENT_SENTINEL", None)


@pytest.fixture(autouse=True)
def _isolate_scar_state(tmp_path_factory, monkeypatch):
    """Strip ambient SCAR_* state, then point SCAR_STATE_DIR at a per-test
    tmp dir, for EVERY test.

    Tests that set SCAR_STATE_DIR themselves still win — monkeypatch.setenv
    inside a test overrides this, so existing explicit isolation is unchanged.

    The strip is by PREFIX rather than by an explicit list (#239). An explicit
    list leaves the same class open that the per-test fixture did: the next
    variable added to the tool is not on it. A test that wants a flag must set
    it, so its expectation is stated rather than inherited from whoever ran
    the suite.
    """
    for name in [k for k in os.environ if k.startswith("SCAR_")]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCAR_STATE_DIR",
                       str(tmp_path_factory.mktemp("scar-state")))
