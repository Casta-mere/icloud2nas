from pathlib import Path

import pytest

from icloud2nas.orchestrator.config import Config, normalize_hhmm


def test_defaults_and_env_overrides(tmp_path: Path) -> None:
    cfg = Config.from_env({"I2N_DATA_DIR": str(tmp_path / "d"), "I2N_PHOTOS_DIR": "/p"})
    assert cfg.data_dir == tmp_path / "d" and cfg.photos_dir == Path("/p")
    assert cfg.db_path == tmp_path / "d" / "icloud2nas.db"
    assert cfg.secret_path == tmp_path / "d" / "secret.key"
    assert cfg.logs_dir == tmp_path / "d" / "logs" / "runs"
    assert cfg.plans_dir == tmp_path / "d" / "plans"
    assert cfg.cookie_dir("acc1") == tmp_path / "d" / "accounts" / "acc1" / "cookies"
    assert cfg.worker_cmd[-2:] == ("-m", "icloud2nas.worker")
    assert cfg.max_workers == 2 and cfg.min_free_gb == 5.0
    assert cfg.default_interval_min == 30 and cfg.default_nightly_at == "03:00"
    assert cfg.host_etc == Path("/host/etc")
    assert cfg.account_plans_dir("acc1") == tmp_path / "d" / "plans" / "acc1"
    assert Config.from_env({"I2N_HOST_ETC": "/x/etc"}).host_etc == Path("/x/etc")


def test_worker_cmd_and_numbers_from_env(tmp_path: Path) -> None:
    cfg = Config.from_env(
        {
            "I2N_DATA_DIR": str(tmp_path),
            "I2N_WORKER_CMD": "/usr/bin/python3 /x/fake.py --flag",
            "I2N_MAX_WORKERS": "3",
            "I2N_MIN_FREE_GB": "1.5",
            "TZ": "UTC",
        }
    )
    assert cfg.worker_cmd == ("/usr/bin/python3", "/x/fake.py", "--flag")
    assert cfg.max_workers == 3 and cfg.min_free_gb == 1.5 and cfg.tz == "UTC"


def test_ensure_layout_creates_dirs(tmp_path: Path) -> None:
    cfg = Config.from_env({"I2N_DATA_DIR": str(tmp_path / "data")})
    cfg.ensure_layout()
    for p in (cfg.logs_dir, cfg.plans_dir, cfg.accounts_dir):
        assert p.is_dir()


def test_nightly_at_is_normalised_and_validated(tmp_path: Path) -> None:
    cfg = Config.from_env({"I2N_DATA_DIR": str(tmp_path), "I2N_DEFAULT_NIGHTLY_AT": "4:5"})
    assert cfg.default_nightly_at == "04:05"
    assert cfg.lock_path == tmp_path / "daemon.lock"
    assert normalize_hhmm("3:00") == "03:00"
    with pytest.raises(ValueError, match="25:00"):
        normalize_hhmm("25:00")


def test_build_state_makes_logs_and_database_private(config: Config) -> None:
    import os

    from icloud2nas.api.state import build_state
    from tests.conftest import make_env

    os.chmod(config.logs_dir.parent, 0o755)
    os.chmod(config.logs_dir, 0o755)
    build_state(config, make_env(config))
    assert config.logs_dir.parent.stat().st_mode & 0o777 == 0o700
    assert config.logs_dir.stat().st_mode & 0o777 == 0o700
    assert config.db_path.stat().st_mode & 0o777 == 0o600
    for suffix in ("-wal", "-shm"):
        side = Path(f"{config.db_path}{suffix}")
        if side.exists():
            assert side.stat().st_mode & 0o777 == 0o600, side
