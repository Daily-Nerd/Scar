"""`.scars/config.json`: the one reader (#392).

The file is optional. Absent, or present without an `org`, a repository does not
sign promotions. A file that is present but cannot be read, or an `org` that is
present but not a non-empty string, is an error and never "no org": reading a
damaged config as unsigned would let a typo, or a deliberate edit, turn signing
off without anyone deciding to.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_NAME = "config.json"


class ConfigError(ValueError):
    """The config exists and says something unusable."""


@dataclass(frozen=True)
class Config:
    org: str | None = None


def load(scars_dir: Path) -> Config:
    path = Path(scars_dir) / CONFIG_NAME
    if not path.exists():
        return Config()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(f"cannot read {CONFIG_NAME}: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_NAME} must be a JSON object")
    org = None
    if "org" in raw:
        org = raw["org"]
        if not isinstance(org, str) or not org:
            raise ConfigError(f"{CONFIG_NAME}: org must be a non-empty string")
    return Config(org=org)
