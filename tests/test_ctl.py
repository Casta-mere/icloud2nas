from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from icloud2nas import ctl
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.store import repo


def _env(config: Config, **extra: str) -> dict[str, str]:
    return {
        "I2N_DATA_DIR": str(config.data_dir),
        "I2N_PHOTOS_DIR": str(config.photos_dir),
        "I2N_WORKER_CMD": " ".join(config.worker_cmd),
        "TZ": "Asia/Shanghai",
        "I2N_JITTER_S": "0,0",
        "I2N_CTL_PASSWORD": "pw",
        **extra,
    }


def _run(
    config: Config, argv: list[str], codes: list[str] | None = None, **extra: str
) -> tuple[int, str]:
    out = io.StringIO()
    queue = list(codes or [])
    rc = ctl.main(argv, env=_env(config, **extra), ask_code=lambda prompt: queue.pop(0), out=out)
    return rc, out.getvalue()


def test_account_add_with_2fa_then_lists_libraries(
    config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_WORKER_CALLS", str(calls))
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps(
            {
                "auth": {
                    "events": [
                        {"ev": "started"},
                        {"ev": "need_mfa"},
                        {"wait_code": "123456"},
                        {"ev": "done", "stats": {}},
                    ],
                    "exit": 0,
                }
            }
        ),
    )
    rc, out = _run(
        config,
        ["account", "add", "--name", "旭刚", "--apple-id", "a@icloud.com", "--region", "cn"],
        codes=["000000", "123456"],
    )
    assert rc == 0, out
    assert "PrimarySync" in out and "account" in out.lower()
    rc, out = _run(config, ["account", "list"])
    assert rc == 0 and "a@icloud.com" in out and "\tok\t" in out
    rc, out = _run(config, ["library", "list"])
    assert rc == 0 and "旭刚" in out and "PrimarySync" in out and "\tTrue\t" in out
    invocations = [json.loads(line) for line in calls.read_text(encoding="utf-8").splitlines()]
    assert [c["argv"][1] for c in invocations] == ["auth", "list"]
    assert (
        invocations[0]["job"]["interactive"] is True
        and invocations[1]["job"]["interactive"] is False
    )


def test_library_set_and_manual_run_and_runs_listing(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    rc, _ = _run(
        config, ["account", "add", "--name", "x", "--apple-id", "b@icloud.com", "--region", "com"]
    )
    assert rc == 0
    app = ctl.build_app(Config.from_env(_env(config)))
    with app.store.session() as s:
        lib = repo.list_libraries(s)[0]
        library_id = lib.id
    rc, out = _run(
        config,
        [
            "library",
            "set",
            library_id[:8],
            "--interval",
            "15",
            "--nightly",
            "04:30",
            "--dir",
            "x-photos",
        ],
    )
    assert rc == 0
    rc, out = _run(config, ["library", "list"])
    assert "\t15\t04:30\t" in out and "x-photos" in out
    rc, out = _run(config, ["library", "set", library_id, "--dir", "/abs"])
    assert rc == 1 and "relative" in out
    rc, out = _run(config, ["run", "sync", library_id])
    assert rc == 0 and "[started]" in out and "[done]" in out
    rc, out = _run(config, ["runs", "--library", library_id])
    assert rc == 0 and "\tsync\tmanual\tok\t" in out
    rc, out = _run(config, ["library", "set", library_id, "--disable"])
    rc, out = _run(config, ["library", "list"])
    assert "\tFalse\t" in out


def test_plan_approve_runs_apply_plan_without_password(
    config: Config, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_WORKER_CALLS", str(calls))
    rc, _ = _run(
        config, ["account", "add", "--name", "x", "--apple-id", "c@icloud.com", "--region", "cn"]
    )
    assert rc == 0
    app = ctl.build_app(Config.from_env(_env(config)))
    with app.store.session() as s:
        library_id = repo.list_libraries(s)[0].id
    plan_file = config.plans_dir / "fused.txt"
    plan_file.write_text("2024/05/a.HEIC\n", encoding="utf-8")
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps(
            {
                "reconcile": {
                    "events": [
                        {"ev": "started"},
                        {
                            "ev": "reconcile_plan",
                            "remote_total": 100,
                            "local_total": 130,
                            "extraneous": 30,
                            "missing": 0,
                            "plan_file": str(plan_file),
                            "fused": True,
                        },
                    ],
                    "exit": 6,
                }
            }
        ),
    )
    rc, out = _run(config, ["run", "reconcile", library_id])
    assert rc == 6
    rc, out = _run(config, ["plan", "list"])
    assert rc == 0 and "pending" in out and "30" in out
    plan_id = out.strip().splitlines()[-1].split("\t")[0]
    monkeypatch.delenv("FAKE_WORKER_SCENARIO")
    rc, out = _run(config, ["plan", "approve", plan_id])
    assert rc == 0
    last = json.loads(calls.read_text(encoding="utf-8").splitlines()[-1])
    assert last["argv"][-2:] == ["--apply-plan", str(plan_file)] and last["has_password"] is False
    rc, out = _run(config, ["plan", "list"])
    assert "pending" not in out
    rc, out = _run(config, ["plan", "list", "--status", "executed"])
    assert plan_id in out


def test_unknown_id_and_bad_args(config: Config) -> None:
    rc, out = _run(config, ["library", "set", "nope", "--enable"])
    assert rc == 1 and "not found" in out
    with pytest.raises(SystemExit):
        ctl.main(["bogus"], env=_env(config))


def test_ambiguous_prefix_is_an_error(config: Config) -> None:
    rc, _ = _run(
        config, ["account", "add", "--name", "a", "--apple-id", "x1@icloud.com", "--region", "cn"]
    )
    assert rc == 0
    rc, _ = _run(
        config, ["account", "add", "--name", "b", "--apple-id", "x2@icloud.com", "--region", "cn"]
    )
    assert rc == 0
    rc, out = _run(config, ["library", "set", "", "--enable"])
    assert rc == 1 and "not found" in out
    app = ctl.build_app(Config.from_env(_env(config)), _env(config))
    with app.store.session() as s:
        ids = sorted(lib.id for lib in repo.list_libraries(s))
    common = ""
    for a, b in zip(ids[0], ids[1], strict=False):
        if a != b:
            break
        common += a
    if common:  # two uuid4 hex ids share a prefix only by chance; skip the assertion otherwise
        rc, out = _run(config, ["library", "set", common, "--enable"])
        assert rc == 1 and "ambiguous" in out
