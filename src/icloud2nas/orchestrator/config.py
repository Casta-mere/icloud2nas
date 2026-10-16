"""Orchestrator configuration from environment variables and the data-directory layout."""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    data_dir: Path
    photos_dir: Path
    worker_cmd: tuple[str, ...]
    tz: str
    max_workers: int = 2
    min_free_gb: float = 5.0
    default_interval_min: int = 30
    default_nightly_at: str = "03:00"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "icloud2nas.db"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret.key"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs" / "runs"

    @property
    def plans_dir(self) -> Path:
        return self.data_dir / "plans"

    @property
    def accounts_dir(self) -> Path:
        return self.data_dir / "accounts"

    def cookie_dir(self, account_id: str) -> Path:
        return self.accounts_dir / account_id / "cookies"

    def ensure_layout(self) -> None:
        for p in (self.data_dir, self.logs_dir, self.plans_dir, self.accounts_dir):
            p.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        source = os.environ if env is None else env
        raw_cmd = source.get("I2N_WORKER_CMD")
        worker_cmd = (
            tuple(shlex.split(raw_cmd)) if raw_cmd else (sys.executable, "-m", "icloud2nas.worker")
        )
        return cls(
            data_dir=Path(source.get("I2N_DATA_DIR", "/data")),
            photos_dir=Path(source.get("I2N_PHOTOS_DIR", "/photos")),
            worker_cmd=worker_cmd,
            tz=source.get("TZ", "UTC"),
            max_workers=int(source.get("I2N_MAX_WORKERS", "2")),
            min_free_gb=float(source.get("I2N_MIN_FREE_GB", "5")),
            default_interval_min=int(source.get("I2N_DEFAULT_INTERVAL_MIN", "30")),
            default_nightly_at=source.get("I2N_DEFAULT_NIGHTLY_AT", "03:00"),
        )
