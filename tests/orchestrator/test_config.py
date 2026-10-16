from pathlib import Path

from icloud2nas.orchestrator.config import Config


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
