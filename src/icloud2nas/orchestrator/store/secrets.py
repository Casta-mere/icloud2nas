"""Password encryption at rest (spec §4): Fernet with a master key kept in the data dir."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from cryptography.fernet import Fernet

from icloud2nas.orchestrator.config import Config

SECRET_ENV = "I2N_SECRET_KEY"


def load_or_create_key(path: Path, env_value: str | None = None) -> bytes:
    """Return the Fernet key: I2N_SECRET_KEY if set, else the key file (created once, 0600)."""
    if env_value:
        return env_value.encode("ascii")
    if path.exists():
        return path.read_bytes().strip()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes().strip()  # another process won the race; use its key
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


class Secrets:
    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    @classmethod
    def from_config(cls, config: Config, env: Mapping[str, str] | None = None) -> Secrets:
        source = os.environ if env is None else env
        return cls(load_or_create_key(config.secret_path, source.get(SECRET_ENV)))

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, token: bytes) -> str:
        return self._fernet.decrypt(token).decode("utf-8")
