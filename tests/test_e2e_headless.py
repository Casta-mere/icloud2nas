"""Whole walkthrough over HTTP: icloud2nas-ctl -> API -> scheduler -> fake worker."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState
from icloud2nas.engine.trash import DAY_MARKER
from icloud2nas.orchestrator.store import repo
from tests.api.conftest import set_scenario, wait_for
from tests.conftest import ctl_run

API = tuple[TestClient, AppState]


def _tick(client: TestClient, state: AppState) -> int:
    launched: int = client.portal.call(state.scheduler.tick)  # type: ignore[union-attr]
    return launched


def _idle(client: TestClient, state: AppState) -> None:
    wait_for(client, lambda c: not state.scheduler.running_libraries and not state.scheduler.tasks)


def test_household_two_accounts_scheduled_fused_plan_trash_and_pause(
    api: API, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, state = api
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_WORKER_CALLS", str(calls))
    set_scenario(
        monkeypatch,
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
        },
    )
    for name, apple_id in (("旭刚", "a@icloud.com"), ("幻伊镜", "b@icloud.com")):
        rc, out = ctl_run(
            client,
            ["account", "add", "--name", name, "--apple-id", apple_id, "--region", "cn"],
            codes=["111111"],
        )
        assert rc == 0, out
        assert "libraries: personal=['PrimarySync']" in out
    monkeypatch.delenv("FAKE_WORKER_SCENARIO")

    rc, out = ctl_run(client, ["library", "list"])
    rows = [line.split("\t") for line in out.strip().splitlines()]
    assert {r[2] for r in rows} == {"旭刚", "幻伊镜"} and {r[4] for r in rows} == {"旭刚", "幻伊镜"}

    # both never-synced libraries are due: the tick starts a full sync for each
    assert _tick(client, state) == 2
    wait_for(
        client,
        lambda c: len([r for r in c.get("/api/runs").json() if r["mode"] == "sync"]) == 2
        and all(r["status"] == "ok" for r in c.get("/api/runs").json() if r["mode"] == "sync"),
    )
    _idle(client, state)
    rc, out = ctl_run(client, ["runs", "--limit", "10"])
    sync_rows = [line.split("\t") for line in out.strip().splitlines() if "\tsync\t" in line]
    assert [r[2:4] for r in sync_rows] == [["schedule", "ok"], ["schedule", "ok"]]
    sync_calls = [
        json.loads(ln)
        for ln in calls.read_text(encoding="utf-8").splitlines()
        if '"--mode", "sync"' in ln
    ]
    assert all(c["job"]["full"] is True for c in sync_calls)
    assert {c["job"]["target_dir"] for c in sync_calls} == {
        str(state.config.photos_dir / "旭刚"),
        str(state.config.photos_dir / "幻伊镜"),
    }
    assert _tick(client, state) == 0  # nothing is due right after a success

    # a fused reconcile -> pending plan -> approve follows to executed (no password for apply)
    plan_file = state.config.plans_dir / "f.txt"
    plan_file.write_text("2024/01/x.HEIC\n", encoding="utf-8")
    set_scenario(
        monkeypatch,
        {
            "reconcile": {
                "events": [
                    {"ev": "started"},
                    {
                        "ev": "reconcile_plan", "remote_total": 50, "local_total": 90,
                        "extraneous": 40, "missing": 0, "plan_file": str(plan_file),
                        "fused": True,
                    },
                ],
                "exit": 6,
            }
        },
    )
    rc, _ = ctl_run(client, ["run", "reconcile", "旭刚"])
    assert rc == 6
    monkeypatch.delenv("FAKE_WORKER_SCENARIO")
    rc, out = ctl_run(client, ["plan", "list"])
    assert rc == 0 and out.count("\n") == 1 and "\tpending\t40\t" in out
    plan_id = out.strip().split("\t")[0]
    rc, out = ctl_run(client, ["plan", "approve", plan_id])
    assert rc == 0 and f"plan {plan_id} executed" in out
    last = json.loads(calls.read_text(encoding="utf-8").splitlines()[-1])
    assert last["argv"][-2:] == ["--apply-plan", str(plan_file)] and last["has_password"] is False
    _idle(client, state)

    # the trash (as left by a worker) is browsable through the CLI
    day_dir = state.config.photos_dir / ".trash" / "旭刚" / "2026-10-20"
    (day_dir / "2024/01").mkdir(parents=True)
    (day_dir / DAY_MARKER).touch()
    (day_dir / "2024/01/x.HEIC").write_bytes(b"x" * 7)
    rc, out = ctl_run(client, ["trash", "list"])
    assert rc == 0 and "旭刚\t2026-10-20\t1\t7" in out
    rc, out = ctl_run(client, ["trash", "day", "旭刚", "2026-10-20"])
    assert "2024/01/x.HEIC\t7" in out

    # pause stops scheduling; resume brings it back
    with state.store.session() as s:
        for lib in repo.list_libraries(s):
            lib.last_success_at = None  # force both due again
    assert ctl_run(client, ["pause"])[0] == 0
    assert client.get("/api/status").json()["paused"] is True
    assert _tick(client, state) == 0
    assert ctl_run(client, ["resume"])[0] == 0
    assert client.get("/api/status").json()["paused"] is False

    # an expired session on scheduled runs parks both accounts; parked accounts are not retried
    set_scenario(
        monkeypatch,
        {"sync": {"events": [{"ev": "started"}, {"ev": "need_mfa"}], "exit": 3}},
    )
    assert _tick(client, state) == 2
    wait_for(
        client,
        lambda c: all(a["status"] == "needs_reauth" for a in c.get("/api/accounts").json()),
    )
    assert _tick(client, state) == 0
    rc, out = ctl_run(client, ["account", "list"])
    assert out.count("needs_reauth") == 2
    assert not [t for t in threading.enumerate() if t.name == "i2n-ctl-events" and t.is_alive()]
