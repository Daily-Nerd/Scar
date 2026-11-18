"""The presence signer and checker: the one place that owns their fixed paths
and the words a promotion act is made of (#392).

Both programs are run only by an absolute path. There is no environment
variable, flag or PATH lookup to move either: a configurable path would let
anything that can edit the environment swap in a signer or checker that always
says yes. Tests replace the module attributes below; production code never does.
"""
from __future__ import annotations

_BIN = "/Library/Application Support/handsal/bin"
SIGNER = f"{_BIN}/handsal-sign"
CHECKER = f"{_BIN}/handsal-check"

# Signed domain separation: renaming CONSUMER makes every act already stored
# consumer_mismatch on recheck-act. It must never change (a test pins it).
CONSUMER = "scar"
ACTION = "promote"
METHOD = "local:handsal.decision"

# How long a freshly signed act stays acceptable to the first-acceptance check.
# A decimal string, as the signer requires. A constant, not config: Scar cannot
# know the org's maximum and keeps no copy of it. An org that allows less than
# this answers validity_too_long at acceptance, after the touch; the remedy is a
# deliberate change to this constant.
EXPIRES_IN_S = "3600"
