from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from icloud2nas.api.state import AppState
from tests.api.conftest import onboard, run_manual, set_scenario, wait_for

API = tuple[TestClient, AppState]


def _ready(client: TestClient) -> tuple[str, str]:
    account_id, library_id = onboard(client)
    wait_for(client, lambda c: c.get(f"/api/accounts/{account_id}").json()["status"] == "ok")
    return account_id, library_id


def test_runs_listing_and_log(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = api
    account_id, library_id = _ready(client)
    set_scenario(
        monkeypatch,
        {"sync": {"events": [{"ev": "started"}, {"stderr": "hello from worker"}], "exit": 0}},
    )
    client.post(f"/api/libraries/{library_id}/sync")
    run_manual(client, state, {"sync"})
    sync = next(r for r in client.get("/api/runs?limit=10").json() if r["mode"] == "sync")
    assert client.get(f"/api/runs/{sync['id'][:8]}").json()["id"] == sync["id"]
    log = client.get(f"/api/runs/{sync['id']}/log")
    assert log.status_code == 200
    assert log.headers["content-type"] == "text/plain; charset=utf-8"
    assert "hello from worker" in log.text
    assert {r["mode"] for r in client.get(f"/api/runs?account_id={account_id}").json()} >= {
        "auth",
        "sync",
    }
    assert [r["id"] for r in client.get(f"/api/runs?library_id={library_id}").json()] == [
        sync["id"]
    ]
    assert len(client.get("/api/runs?limit=1").json()) == 1
    assert client.get("/api/runs/nope").status_code == 404
    auth = next(r for r in client.get("/api/runs").json() if r["mode"] == "auth")
    state_log = auth["id"]
    with state.store.session() as s:
        from icloud2nas.orchestrator.store import repo

        run = repo.get_run(s, state_log)
        assert run is not None
        run.log_path = None
    assert client.get(f"/api/runs/{state_log}/log").status_code == 404


def test_plan_detail_approve_reject(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = api
    _, library_id = _ready(client)
    plan_file = state.config.plans_dir / "f.txt"
    plan_file.write_text("2024/01/x.HEIC\n2024/01/y.HEIC\n", encoding="utf-8")
    fused = {
        "reconcile": {
            "events": [
                {"ev": "started"},
                {
                    "ev": "reconcile_plan",
                    "remote_total": 50,
                    "local_total": 52,
                    "extraneous": 2,
                    "missing": 0,
                    "plan_file": str(plan_file),
                    "fused": True,
                },
            ],
            "exit": 6,
        }
    }
    set_scenario(monkeypatch, fused)
    client.post(f"/api/libraries/{library_id}/reconcile")
    run_manual(client, state, {"reconcile"})
    monkeypatch.delenv("FAKE_WORKER_SCENARIO")
    pending = client.get("/api/plans").json()
    assert len(pending) == 1 and "paths" not in pending[0]
    plan_id = pending[0]["id"]
    detail = client.get(f"/api/plans/{plan_id[:8]}").json()
    assert detail["paths"] == ["2024/01/x.HEIC", "2024/01/y.HEIC"] and detail["total"] == 2
    assert client.get("/api/plans?status=bogus").status_code == 400

    approved = client.post(f"/api/plans/{plan_id}/approve")
    assert approved.status_code == 202 and approved.json()["status"] == "approved"
    client.portal.call(state.scheduler.tick)  # type: ignore[union-attr]
    wait_for(
        client, lambda c: c.get(f"/api/plans/{plan_id}").json()["status"] == "executed"
    )
    assert client.post(f"/api/plans/{plan_id}/reject").status_code == 400
    assert client.get("/api/plans").json() == []
    assert [p["id"] for p in client.get("/api/plans?status=all").json()] == [plan_id]


def test_reject_pending_plan(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = api
    _, library_id = _ready(client)
    plan_file = state.config.plans_dir / "g.txt"
    plan_file.write_text("a.jpg\n", encoding="utf-8")
    set_scenario(
        monkeypatch,
        {
            "reconcile": {
                "events": [
                    {"ev": "started"},
                    {
                        "ev": "reconcile_plan", "remote_total": 1, "local_total": 2,
                        "extraneous": 1, "missing": 0, "plan_file": str(plan_file), "fused": True,
                    },
                ],
                "exit": 6,
            }
        },
    )
    client.post(f"/api/libraries/{library_id}/reconcile")
    run_manual(client, state, {"reconcile"})
    plan_id = client.get("/api/plans").json()[0]["id"]
    rejected = client.post(f"/api/plans/{plan_id}/reject")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert client.post(f"/api/plans/{plan_id}/approve").status_code == 400
