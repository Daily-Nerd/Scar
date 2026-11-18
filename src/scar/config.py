"""`.scars/config.json`: the one reader (#392).

The file is optional. Absent, or present without an `org`, a repository does not
sign promotions. A file that is present but cannot be read, or an `org` that is
present but not a non-empty string, is an error and never "no org": reading a
damaged config as unsigned would let a typo, or a deliberate edit, turn signing
off without anyone deciding to.

`signed_from` is the first scar id whose promotion must carry a signed act. It
is the marker that tells lint which scars were promoted under the org: a scar's
own file cannot say so, because whoever strips the act could strip the claim
too. The config lives in git, so moving the marker shows in a pull request.
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
    signed_from: int | None = None


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
    signed_from = None
    if "signed_from" in raw:
        signed_from = raw["signed_from"]
        # bool is an int in Python; `true` is not a scar id.
        if type(signed_from) is not int or signed_from < 1:
            raise ConfigError(f"{CONFIG_NAME}: signed_from must be a whole number of 1 or more")
    return Config(org=org, signed_from=signed_from)


def signing_problem(cfg: Config, next_id: int) -> str | None:
    """Why this config cannot govern a signed repository, or None. One owner of
    the rule for both lint and promote, so promote never creates a state lint
    would refuse. Only meaningful when an org is named."""
    if cfg.org is None:
        return None
    if cfg.signed_from is None:
        return (f"{CONFIG_NAME} names an org but not signed_from; set signed_from to "
                f"the first scar id that needs a signed act (next id is {next_id})")
    if cfg.signed_from > next_id:
        return (f"{CONFIG_NAME}: signed_from {cfg.signed_from} is beyond the next id "
                f"{next_id}, so scars promoted before it would need no signed act")
    return None
