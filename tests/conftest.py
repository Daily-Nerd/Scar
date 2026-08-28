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

import pytest


@pytest.fixture(autouse=True)
def _isolate_scar_state(tmp_path_factory, monkeypatch):
    """Point SCAR_STATE_DIR at a per-test tmp dir for EVERY test.

    Tests that set SCAR_STATE_DIR themselves still win — monkeypatch.setenv
    inside a test overrides this, so existing explicit isolation is unchanged.
    """
    monkeypatch.setenv("SCAR_STATE_DIR",
                       str(tmp_path_factory.mktemp("scar-state")))
