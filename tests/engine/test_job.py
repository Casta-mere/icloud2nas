import json

import pytest

from icloud2nas.engine.job import JOB_ENV, FilePolicy, JobSpec

BASE = {
    "account_id": "acc1",
    "apple_id": "a@icloud.com",
    "region": "cn",
    "client_id": "11111111-1111-1111-1111-111111111111",
    "cookie_dir": "/data/accounts/acc1/cookies",
}


def test_policy_defaults() -> None:
    p = FilePolicy()
    assert p.folder_structure == "{:%Y/%m}"
    assert p.file_match_policy == "name-id7"
    assert p.live_photo_mov_filename_policy == "original"
    assert p.keep_unicode_in_filenames is True
    assert p.threads_num == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"file_match_policy": "nope"},
        {"live_photo_mov_filename_policy": "x"},
        {"threads_num": 0},
        {"unknown_key": 1},
    ],
)
def test_policy_rejects_bad_values(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        FilePolicy.from_dict(bad)


def test_load_auth_minimal() -> None:
    spec = JobSpec.load("auth", "run-1", json.dumps({**BASE, "interactive": True}))
    assert spec.mode == "auth" and spec.run_id == "run-1"
    assert spec.interactive is True and spec.is_primary is True
    assert spec.policy == FilePolicy()


def test_load_rejects_bad_mode_and_region() -> None:
    with pytest.raises(ValueError):
        JobSpec.load("fly", "r", json.dumps(BASE))
    with pytest.raises(ValueError):
        JobSpec.load("auth", "r", json.dumps({**BASE, "region": "jp"}))


def test_load_rejects_missing_required_and_unknown_keys() -> None:
    with pytest.raises(ValueError, match="client_id"):
        JobSpec.load("auth", "r", json.dumps({k: v for k, v in BASE.items() if k != "client_id"}))
    with pytest.raises(ValueError, match="unknown"):
        JobSpec.load("auth", "r", json.dumps({**BASE, "colour": "red"}))


def test_sync_requires_absolute_dirs_inside_photos_root() -> None:
    ok = JobSpec.load(
        "sync",
        "r",
        json.dumps({**BASE, "photos_root": "/photos", "target_dir": "/photos/旭刚/"}),
    )
    assert ok.library_rel == "旭刚"
    assert ok.until_found == 50 and ok.full is False
    with pytest.raises(ValueError):
        JobSpec.load("sync", "r", json.dumps({**BASE, "photos_root": "/photos"}))
    with pytest.raises(ValueError):
        JobSpec.load(
            "sync", "r", json.dumps({**BASE, "photos_root": "/photos", "target_dir": "rel/dir"})
        )
    with pytest.raises(ValueError):
        JobSpec.load(
            "sync",
            "r",
            json.dumps({**BASE, "photos_root": "/photos", "target_dir": "/elsewhere/x"}),
        )


def test_shared_library_rel_and_zone() -> None:
    spec = JobSpec.load(
        "sync",
        "r",
        json.dumps(
            {
                **BASE,
                "photos_root": "/photos",
                "target_dir": "/photos/shared/家庭",
                "library_zone": "SharedSync-ABC",
                "full": True,
            }
        ),
    )
    assert spec.is_primary is False and spec.library_rel == "shared/家庭"
    assert spec.full is True


def test_reconcile_needs_plan_dir_unless_applying() -> None:
    body = {**BASE, "photos_root": "/photos", "target_dir": "/photos/a"}
    with pytest.raises(ValueError, match="plan_dir"):
        JobSpec.load("reconcile", "r", json.dumps(body))
    ok = JobSpec.load("reconcile", "r", json.dumps({**body, "plan_dir": "/data/plans"}))
    assert ok.plan_dir == "/data/plans"
    applying = JobSpec.load("reconcile", "r", json.dumps(body), apply_plan="/data/plans/r.txt")
    assert applying.apply_plan == "/data/plans/r.txt"


def test_from_env_reads_job_env() -> None:
    env = {JOB_ENV: json.dumps({**BASE, "policy": {"threads_num": 4}})}
    spec = JobSpec.from_env("list", "r", env=env)
    assert spec.policy.threads_num == 4
    with pytest.raises(ValueError, match=JOB_ENV):
        JobSpec.from_env("list", "r", env={})
