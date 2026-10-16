import sys
from pathlib import Path

import pytest

from icloud2nas.orchestrator.config import Config

FAKE_WORKER = Path(__file__).resolve().parents[1] / "fake_worker.py"


@pytest.fixture
def config(tmp_path: Path) -> Config:
    cfg = Config.from_env(
        {
            "I2N_DATA_DIR": str(tmp_path / "data"),
            "I2N_PHOTOS_DIR": str(tmp_path / "photos"),
            "I2N_WORKER_CMD": f"{sys.executable} {FAKE_WORKER}",
            "TZ": "Asia/Shanghai",
        }
    )
    cfg.ensure_layout()
    (tmp_path / "photos").mkdir(exist_ok=True)
    return cfg
