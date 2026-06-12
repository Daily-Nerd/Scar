#!/usr/bin/env python3
"""Backward-compatible wrapper for the packaged hook lifecycle commands."""

import sys

from scar.installer import find_scar, install, status, uninstall


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry-run" in args
    cmd = next((a for a in args if not a.startswith("-")), "status")
    sys.exit({"install": lambda: install(dry=dry),
              "uninstall": lambda: uninstall(dry=dry),
              "status": status}.get(cmd, status)())
