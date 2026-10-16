"""Whole-core walkthrough: add account, libraries, scheduled runs, fuse, approve (fake worker)."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest

from icloud2nas import ctl
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.store import repo


def _env(config: Config) -> dict[str, str]:
    return {
        "I2N_DATA_DIR": str(config.data_dir),
        "I2N_PHOTOS_DIR": str(config.photos_dir),
        "I2N_WORKER_CMD": " ".join(config.worker_cmd),
        "TZ": "Asia/Shanghai",
        "I2N_JITTER_S": "0,0",
        "I2N_CTL_PASSWORD": "pw",
    }


async def _ctl(config: Config, argv: list[str], codes: list[str] | None = None) -> tuple[int, str]:
    # ctl.main drives its own asyncio.run, so it must run off the test's event loop
    out = io.StringIO()
    queue = list(codes or [])
    rc = await asyncio.to_thread(
        ctl.main, argv, env=_env(config), ask_code=lambda prompt: queue.pop(0), out=out
    )
    return rc, out.getvalue()


async def test_household_two_accounts_scheduled_and_fused_plan(
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
                        {"wait_code": "111111"},
                        {"ev": "done", "stats": {}},
                    ],
                    "exit": 0,
                }
            }
        ),
    )
    assert (
        await _ctl(
            config,
            ["account", "add", "--name", "旭刚", "--apple-id", "a@icloud.com", "--region", "cn"],
            ["111111"],
        )
    )[0] == 0
    assert (
        await _ctl(
            config,
            ["account", "add", "--name", "幻伊镜", "--apple-id", "b@icloud.com", "--region", "cn"],
            ["111111"],
        )
    )[0] == 0
    monkeypatch.delenv("FAKE_WORKER_SCENARIO")

    app = ctl.build_app(Config.from_env(_env(config)), _env(config))
    with app.store.session() as s:
        libs = {lib.display_name: lib for lib in repo.list_libraries(s)}
        assert set(libs) == {"旭刚", "幻伊镜"} and all(lib.enabled for lib in libs.values())
        assert {lib.target_dir for lib in libs.values()} == {"旭刚", "幻伊镜"}

    # first tick: both never-synced libraries get a full sync, in parallel (max_workers=2)
    launched = await app.scheduler.tick()
    assert launched == 2
    await asyncio.gather(*app.scheduler.tasks)
    with app.store.session() as s:
        for lib in repo.list_libraries(s):
            assert lib.last_success_at is not None
        assert all(r.status == "ok" and r.mode == "sync" for r in repo.list_runs(s) if r.library_id)
    sync_calls = [
        json.loads(ln)
        for ln in calls.read_text(encoding="utf-8").splitlines()
        if '"--mode", "sync"' in ln
    ]
    assert all(c["job"]["full"] is True for c in sync_calls)
    assert {c["job"]["target_dir"] for c in sync_calls} == {
        str(config.photos_dir / "旭刚"),
        str(config.photos_dir / "幻伊镜"),
    }

    # nothing due right after a success
    assert await app.scheduler.tick() == 0

    # a fused reconcile produces a pending plan; approving it runs --apply-plan without a password
    plan_file = config.plans_dir / "f.txt"
    plan_file.write_text("2024/01/x.HEIC\n", encoding="utf-8")
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps(
            {
                "reconcile": {
                    "events": [
                        {"ev": "started"},
                        {
                            "ev": "reconcile_plan",
                            "remote_total": 50,
                            "local_total": 90,
                            "extraneous": 40,
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
    with app.store.session() as s:
        lib_id = repo.list_libraries(s)[0].id
    rc, _ = await _ctl(config, ["run", "reconcile", lib_id])
    assert rc == 6
    monkeypatch.delenv("FAKE_WORKER_SCENARIO")
    rc, out = await _ctl(config, ["plan", "list"])
    plan_id = out.strip().splitlines()[-1].split("\t")[0]
    assert (await _ctl(config, ["plan", "approve", plan_id]))[0] == 0
    last = json.loads(calls.read_text(encoding="utf-8").splitlines()[-1])
    assert last["argv"][-2:] == ["--apply-plan", str(plan_file)] and last["has_password"] is False

    # an expired session on a scheduled run parks the account, the other account keeps going
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"sync": {"events": [{"ev": "started"}, {"ev": "need_mfa"}], "exit": 3}}),
    )
    with app.store.session() as s:
        for lib in repo.list_libraries(s):
            lib.last_success_at = None  # force both due again
    assert await app.scheduler.tick() == 2
    await asyncio.gather(*app.scheduler.tasks)
    with app.store.session() as s:
        assert all(a.status == "needs_reauth" for a in repo.list_accounts(s))
    assert await app.scheduler.tick() == 0  # parked accounts are skipped
    rc, out = await _ctl(config, ["account", "list"])
    assert out.count("needs_reauth") == 2
