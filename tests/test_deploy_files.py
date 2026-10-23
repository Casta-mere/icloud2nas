"""Static checks of the image and compose files (they are not exercised by the unit tests)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("env", [{"PUID": "1000"}, {"PGID": "10"}])
def test_entrypoint_refuses_half_of_puid_pgid(env: dict[str, str]) -> None:
    clean = {k: v for k, v in os.environ.items() if k not in ("PUID", "PGID")}
    done = subprocess.run(
        ["sh", str(ROOT / "docker" / "entrypoint.sh")],
        env={**clean, **env}, capture_output=True, text=True, timeout=10, check=False,
    )
    assert done.returncode == 1
    assert "PUID and PGID" in done.stderr


def test_healthcheck_honours_the_port_and_waits_for_startup() -> None:
    line = next(
        line for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.startswith("HEALTHCHECK")
    )
    assert "--start-period=30s" in line and "${I2N_PORT:-4871}" in line
    assert " CMD [" not in line  # shell form, so the variable is expanded


def test_compose_example_requires_photos_root() -> None:
    text = (ROOT / "compose.example.yaml").read_text(encoding="utf-8")
    assert "${PHOTOS_ROOT:?" in text
