import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken

from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.store.secrets import Secrets, load_or_create_key


def test_key_is_created_once_with_0600(tmp_path: Path) -> None:
    path = tmp_path / "secret.key"
    first = load_or_create_key(path)
    assert path.exists() and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert load_or_create_key(path) == first
    Fernet(first)  # valid key format


def test_env_key_wins_and_is_not_written(tmp_path: Path) -> None:
    path = tmp_path / "secret.key"
    env_key = Fernet.generate_key().decode()
    assert load_or_create_key(path, env_key) == env_key.encode()
    assert not path.exists()


def test_round_trip_and_wrong_key(config: Config) -> None:
    secrets = Secrets.from_config(config)
    token = secrets.encrypt("p@ss 密码")
    assert token != "p@ss 密码".encode() and secrets.decrypt(token) == "p@ss 密码"
    other = Secrets(Fernet.generate_key())
    with pytest.raises(InvalidToken):
        other.decrypt(token)


def test_concurrent_creation_uses_the_existing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os as real_os

    from icloud2nas.orchestrator.store import secrets as mod

    path = tmp_path / "secret.key"
    other = Fernet.generate_key()
    original_open = real_os.open

    def racing_open(p: object, flags: int, mode: int = 0o777) -> int:
        path.write_bytes(other)  # the "other process" writes first
        return original_open(p, flags, mode)

    monkeypatch.setattr(mod.os, "open", racing_open)
    assert load_or_create_key(path) == other
