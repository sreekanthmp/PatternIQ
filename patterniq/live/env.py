"""Credentials from the environment (or a git-ignored ``.env``), and keeping them out of every message."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


class LiveConfigError(ValueError):
    """Live-data configuration is incomplete; the message names the missing variables (never their values)."""


def load_dotenv(path: str | os.PathLike = ".env") -> list[str]:
    """Read KEY=VALUE lines into ``os.environ`` without overriding variables that are already set.

    Returns the names that were set from the file. Values are never returned or printed.
    """
    p = Path(path)
    if not p.is_file():
        return []
    loaded: list[str] = []
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def redact(text: object, secrets: list[str] | tuple[str, ...]) -> str:
    """Replace every known secret (and anything that looks like a JWT) in ``text`` with ***."""
    out = str(text)
    for s in sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True):
        out = out.replace(s, "***")
    return re.sub(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]*", "***jwt***", out)


@dataclass(frozen=True)
class AngelCredentials:
    api_key: str
    client_id: str
    password: str
    totp_secret: str

    def __repr__(self) -> str:  # never let a stray print()/log of this object leak anything
        return "AngelCredentials(client_id=***, api_key=***, password=***, totp_secret=***)"

    __str__ = __repr__

    def secrets(self) -> list[str]:
        return [self.api_key, self.password, self.totp_secret, self.client_id]

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AngelCredentials":
        e = os.environ if env is None else env
        names = {"api_key": "ANGEL_API_KEY", "client_id": "ANGEL_CLIENT_ID", "password": "ANGEL_PASSWORD", "totp_secret": "ANGEL_TOTP"}
        values = {k: (e.get(v) or "").strip() for k, v in names.items()}
        missing = [names[k] for k, v in values.items() if not v]
        if missing:
            raise LiveConfigError(
                "Missing " + ", ".join(missing) + ". Set them as environment variables or in a git-ignored `.env` file "
                "next to where you run patterniq (see .env.example). ANGEL_TOTP is the TOTP *secret* (the long "
                "key shown when you enrolled), not the 6-digit code."
            )
        return cls(**values)
