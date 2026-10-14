import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import probe  # noqa: E402


def test_build_env_sets_job_and_dirs(tmp_path: Path) -> None:
    env = probe.build_env("a@icloud.com", "cn", tmp_path, mode_extras={"recent": 5})
    job = json.loads(env["I2N_JOB"])
    assert job["apple_id"] == "a@icloud.com" and job["region"] == "cn"
    assert job["cookie_dir"] == str(tmp_path / "cookies" / "a@icloud.com")
    assert job["photos_root"] == str(tmp_path / "photos")
    assert job["target_dir"] == str(tmp_path / "photos" / "a")
    assert job["plan_dir"] == str(tmp_path / "plans")
    assert job["recent"] == 5 and job["interactive"] is True
    assert len(job["client_id"]) == 36
    assert env["CLIENT_ID"] == job["client_id"]


def test_client_id_is_stable_per_apple_id(tmp_path: Path) -> None:
    a = json.loads(probe.build_env("a@icloud.com", "cn", tmp_path)["I2N_JOB"])["client_id"]
    b = json.loads(probe.build_env("a@icloud.com", "cn", tmp_path)["I2N_JOB"])["client_id"]
    assert a == b


def test_compare_reports_common_shared_zones(tmp_path: Path, capsys: object) -> None:
    (tmp_path / "a.json").write_text(
        json.dumps({"apple_id": "a", "shared": ["SharedSync-1", "SharedSync-2"]})
    )
    (tmp_path / "b.json").write_text(json.dumps({"apple_id": "b", "shared": ["SharedSync-2"]}))
    assert probe.compare(tmp_path / "a.json", tmp_path / "b.json") == ["SharedSync-2"]
