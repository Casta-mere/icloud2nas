from __future__ import annotations

import io
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from icloud2nas import ctl, ctl_follow
from icloud2nas.api.state import AppState
from icloud2nas.engine.trash import DAY_MARKER
from tests.api.conftest import PASSWORD, onboard, set_scenario, wait_for
from tests.conftest import ctl_run

API = tuple[TestClient, AppState]
ADD = ["account", "add", "--name", "旭刚", "--apple-id", "a@icloud.com", "--region", "cn"]
FUSED_PLAN = {
    "ev": "reconcile_plan", "remote_total": 100, "local_total": 130, "extraneous": 30,
    "missing": 0, "fused": True,
}


def test_parse_sse_frames() -> None:
    lines = [
        ": ping",
        "event: hello",
        'data: {"server_time": "t"}',
        "",
        "event: run.event",
        'data: {"run_id": "r1",',
        'data: "event": {"ev": "started"}}',
        "",
        ": heartbeat",
        "",
        "event: broken",
        "data: not json",
        "",
        'data: {"no_event": true}',
        "",
    ]
    assert list(ctl.parse_sse(lines)) == [
        ("hello", {"server_time": "t"}),
        ("run.event", {"run_id": "r1", "event": {"ev": "started"}}),
        ("message", {"no_event": True}),
    ]


def test_account_add_with_2fa_over_http(
    api: API, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, _ = api
    calls = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_WORKER_CALLS", str(calls))
    set_scenario(
        monkeypatch,
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
        },
    )
    rc, out = ctl_run(client, [*ADD, "--dir", "xugang-photos"], codes=["000000", "123456"])
    assert rc == 0, out
    assert "created; logging in" in out and "[need_mfa]" in out
    assert out.count("[mfa_result]") == 2 and '"ok": false' in out
    assert "libraries: personal=['PrimarySync'] shared=[]" in out
    rc, out = ctl_run(client, ["account", "list"])
    assert rc == 0 and "a@icloud.com" in out and "\tok\t" in out
    rc, out = ctl_run(client, ["library", "list"])
    assert rc == 0 and "旭刚" in out and "PrimarySync" in out and "xugang-photos" in out
    assert "\tTrue\t" in out
    modes = [json.loads(ln)["argv"][1] for ln in calls.read_text(encoding="utf-8").splitlines()]
    assert modes == ["auth", "list"]


def test_wrong_password_exit_is_reported(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    set_scenario(
        monkeypatch,
        {"auth": {"events": [{"ev": "error", "kind": "bad_password", "message": "no"}],
                  "exit": 4}},
    )
    rc, out = ctl_run(client, ADD)
    assert rc == 4 and "login failed with exit code 4" in out
    rc, out = ctl_run(client, ["account", "list"])
    assert "password_rejected" in out


def test_run_sync_streams_events_and_exit_code(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    _, library_id = onboard(client)
    rc, out = ctl_run(client, ["run", "sync", library_id[:8]])
    assert rc == 0, out
    assert "[started]" in out and "[done]" in out
    set_scenario(
        monkeypatch,
        {"sync": {"events": [{"ev": "started"},
                             {"ev": "error", "kind": "unavailable", "message": "503"}],
                  "exit": 5}},
    )
    rc, out = ctl_run(client, ["run", "sync", "旭刚"])  # by display name
    assert rc == 5 and "[error]" in out
    rc, out = ctl_run(client, ["runs", "--library", library_id, "--limit", "5"])
    assert rc == 0
    rows = [line.split("\t") for line in out.strip().splitlines()]
    assert [r[1:4] for r in rows] == [["sync", "manual", "failed"], ["sync", "manual", "ok"]]
    assert rows[0][6] == "5" and rows[1][6] == "0"


def test_run_refused_when_library_disabled(api: API) -> None:
    client, _ = api
    _, library_id = onboard(client)
    assert ctl_run(client, ["library", "set", library_id, "--disable"])[0] == 0
    rc, out = ctl_run(client, ["run", "sync", library_id])
    assert rc == 1 and out.startswith("error: ") and "disabled" in out


def test_plan_approve_follows_to_executed(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = api
    _, library_id = onboard(client)
    plan_file = state.config.plans_dir / "fused.txt"
    plan_file.write_text("2024/05/a.HEIC\n2024/05/b.HEIC\n", encoding="utf-8")
    set_scenario(
        monkeypatch,
        {"reconcile": {"events": [{"ev": "started"}, {**FUSED_PLAN, "plan_file": str(plan_file)}],
                       "exit": 6}},
    )
    rc, _ = ctl_run(client, ["run", "reconcile", library_id])
    assert rc == 6
    rc, out = ctl_run(client, ["plan", "list"])
    assert rc == 0 and "\tpending\t30\t" in out
    plan_id = out.strip().splitlines()[-1].split("\t")[0]
    rc, out = ctl_run(client, ["plan", "show", plan_id[:8]])
    assert rc == 0 and "2024/05/a.HEIC" in out and "2 paths" in out
    monkeypatch.delenv("FAKE_WORKER_SCENARIO")
    rc, out = ctl_run(client, ["plan", "approve", plan_id[:8]])
    assert rc == 0, out
    assert f"plan {plan_id} executed" in out
    assert ctl_run(client, ["plan", "list"])[1] == ""
    rc, out = ctl_run(client, ["plan", "list", "--status", "executed"])
    assert plan_id in out
    rc, out = ctl_run(client, ["plan", "approve", plan_id])
    assert rc == 1 and out.startswith("error: ")


def test_plan_reject_and_superseded(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, state = api
    _, library_id = onboard(client)
    plan_file = state.config.plans_dir / "r.txt"
    plan_file.write_text("a.jpg\n", encoding="utf-8")
    set_scenario(
        monkeypatch,
        {"reconcile": {"events": [{"ev": "started"}, {**FUSED_PLAN, "plan_file": str(plan_file)}],
                       "exit": 6}},
    )
    assert ctl_run(client, ["run", "reconcile", library_id])[0] == 6
    assert ctl_run(client, ["run", "reconcile", library_id])[0] == 6
    rc, out = ctl_run(client, ["plan", "list", "--status", "superseded"])
    old_id = out.strip().split("\t")[0]
    rc, out = ctl_run(client, ["plan", "approve", old_id])
    assert rc == 1 and "superseded" in out
    new_id = ctl_run(client, ["plan", "list"])[1].strip().split("\t")[0]
    rc, out = ctl_run(client, ["plan", "reject", new_id])
    assert rc == 0 and "rejected" in out
    assert "rejected" in ctl_run(client, ["plan", "list", "--status", "rejected"])[1]


def test_admin_password_prompt_and_retry(api: API) -> None:
    client, _ = api
    assert client.put("/api/settings", json={"admin_password": PASSWORD}).status_code == 200
    rc, out = ctl_run(client, ["status"], password="wrong")
    assert rc == 1 and "wrong admin password" in out
    rc, out = ctl_run(client, ["status"], password=PASSWORD)  # prompt, login, retry
    assert rc == 0 and "paused\tFalse" in out
    assert ctl_run(client, ["status"], password="never asked")[0] == 0  # cookie is kept
    client.cookies.clear()
    rc, out = ctl_run(client, ["pause"], env={"I2N_ADMIN_PASSWORD": PASSWORD})
    assert rc == 0 and "paused: true" in out


def test_event_stream_login_when_admin_password_is_set(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = api
    client.put("/api/settings", json={"admin_password": PASSWORD})
    client.cookies.clear()
    rc, out = ctl_run(client, ADD, password=PASSWORD)
    assert rc == 0, out
    assert "libraries: personal=['PrimarySync']" in out


def test_unknown_id_is_rc_1_with_message(api: API) -> None:
    client, _ = api
    rc, out = ctl_run(client, ["library", "set", "nope", "--enable"])
    assert rc == 1 and out.startswith("error: not found") and "nope" in out
    rc, out = ctl_run(client, ["library", "set", "", "--enable"])
    assert rc == 1 and "must not be empty" in out
    rc, out = ctl_run(client, ["account", "reauth", "nope"])
    assert rc == 1 and "not found" in out
    with pytest.raises(SystemExit):
        ctl.main(["bogus"], env={"I2N_URL": "http://testserver"}, client=client)


def test_unreachable_server_is_rc_1() -> None:
    import io

    out = io.StringIO()
    rc = ctl.main(["status"], env={"I2N_URL": "http://127.0.0.1:9"}, out=out)
    assert rc == 1 and "cannot reach http://127.0.0.1:9" in out.getvalue()


def test_unreachable_server_during_follow_is_rc_1_without_traceback() -> None:
    import io

    env = {"I2N_URL": "http://127.0.0.1:9", "I2N_CTL_PASSWORD": "pw"}
    for argv in (ADD, ["account", "reauth", "x"], ["run", "sync", "x"]):
        out = io.StringIO()
        assert ctl.main(argv, env=env, out=out) == 1
        assert out.getvalue().startswith("error: cannot reach http://127.0.0.1:9")
    # the stream path itself (ensure_session before the event stream), past id resolution
    out = io.StringIO()
    api = ctl.Api(ctl.httpx.Client(), "http://127.0.0.1:9", {}, lambda prompt: "")
    with pytest.raises(ctl.CtlError, match="cannot reach"):
        ctl.follow_run(api, "lib", lambda: None, out)


def test_eof_and_interrupt_at_the_code_prompt_exit_cleanly(
    api: API, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    client, _ = api
    set_scenario(
        monkeypatch,
        {"auth": {"events": [{"ev": "started"}, {"ev": "need_mfa"}, {"wait_code": "1"}],
                  "exit": 0}},
    )

    def run(exc: BaseException) -> tuple[int, str]:
        def ask(prompt: str) -> str:
            raise exc

        out = io.StringIO()
        rc = ctl.main(
            ["account", "add", "--name", str(id(exc)), "--apple-id", f"{id(exc)}@x.com",
             "--region", "cn"],
            env={"I2N_URL": "http://testserver", "I2N_CTL_PASSWORD": "pw"},
            ask_code=ask, out=out, client=client,
        )
        return rc, out.getvalue()

    rc, out = run(EOFError())
    assert rc == 1 and "input ended" in out
    rc, out = run(KeyboardInterrupt())
    assert rc == 130 and "interrupted" in out
    for account in client.get("/api/accounts").json():  # let the abandoned logins finish
        client.post(f"/api/accounts/{account['id']}/code", json={"code": "1"})
    wait_for(
        client,
        lambda c: all(
            a["status"] != "awaiting_code" for a in c.get("/api/accounts").json()
        ),
    )
    wait_for(client, lambda c: not api[1].ops.discovering)


def test_library_set_dir_nightly_pause_and_errors(api: API) -> None:
    client, _ = api
    _, library_id = onboard(client)
    rc, out = ctl_run(
        client,
        ["library", "set", library_id[:8], "--interval", "15", "--nightly", "3:30",
         "--dir", "x-photos", "--name", "Mine"],
    )
    assert rc == 0, out
    rc, out = ctl_run(client, ["library", "list"])
    assert "\tMine\t" in out and "\t15\t03:30\t" in out and "x-photos" in out
    rc, out = ctl_run(client, ["library", "set", library_id, "--dir", "/abs"])
    assert rc == 1 and out.startswith("error: ")
    rc, out = ctl_run(client, ["library", "set", library_id, "--nightly", "25:00"])
    assert rc == 1 and "25:00" in out
    rc, out = ctl_run(client, ["library", "set", library_id])
    assert rc == 1 and "nothing to set" in out
    assert ctl_run(client, ["library", "pause", "Mine"])[0] == 0
    assert "paused" in ctl_run(client, ["library", "list"])[1]
    assert ctl_run(client, ["library", "resume", "Mine"])[0] == 0
    assert ctl_run(client, ["library", "set", library_id, "--disable"])[0] == 0
    assert "\tFalse\t" in ctl_run(client, ["library", "list"])[1]


def test_account_refresh_delete_and_duplicate(api: API) -> None:
    client, _ = api
    account_id, _ = onboard(client)
    rc, out = ctl_run(client, ["account", "refresh", "a@icloud.com"])
    assert rc == 0 and "libraries: personal=['PrimarySync']" in out
    rc, out = ctl_run(client, ADD)
    assert rc == 1 and out.startswith("error: ")  # same Apple ID again
    rc, out = ctl_run(client, ["account", "delete", account_id[:8], "--delete-dir"])
    assert rc == 0 and "deleted" in out
    assert ctl_run(client, ["account", "list"])[1] == ""


def test_account_reauth_and_set_password(api: API, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = api
    account_id, _ = onboard(client)
    wait_for(client, lambda c: c.get(f"/api/accounts/{account_id}").json()["status"] == "ok")
    rc, out = ctl_run(client, ["account", "reauth", "旭刚"])
    assert rc == 0, out
    assert "[started]" in out
    rc, out = ctl_run(client, ["account", "set-password", account_id[:8]], password="new")
    assert rc == 0, out


def test_settings_status_pause_and_trash_commands(api: API, tmp_path: Path) -> None:
    client, state = api
    rc, out = ctl_run(client, ["settings", "set", "trash_retention_days", "45"])
    assert rc == 0, out
    rc, out = ctl_run(client, ["settings", "get"])
    keys = [line.split("\t")[0] for line in out.strip().splitlines()]
    assert rc == 0 and keys == sorted(keys) and "trash_retention_days\t45" in out
    rc, out = ctl_run(client, ["settings", "set", "admin_password_hash", "x"])
    assert rc == 1 and "cannot be set directly" in out
    assert ctl_run(client, ["pause"])[0] == 0
    assert "paused\tTrue" in ctl_run(client, ["status"])[1]
    assert "paused: false" in ctl_run(client, ["resume"])[1]

    _, library_id = onboard(client)
    target = client.get(f"/api/libraries/{library_id}").json()["target_dir"]
    day_dir = state.config.photos_dir / target / ".icloud2nas" / "trash" / "2026-10-20"
    trashed = day_dir / "2024" / "a.HEIC"
    trashed.parent.mkdir(parents=True)
    (day_dir / DAY_MARKER).touch()
    trashed.write_bytes(b"x" * 10)
    rc, out = ctl_run(client, ["trash", "list"])
    assert rc == 0 and "旭刚\t2026-10-20\t1\t10" in out
    rc, out = ctl_run(client, ["trash", "day", library_id[:8], "2026-10-20"])
    assert rc == 0 and "2024/a.HEIC\t10" in out
    rc, out = ctl_run(client, ["trash", "keep", "旭刚", "2026-10-20", "2024/a.HEIC"])
    assert rc == 0 and "kept 2024/a.HEIC" in out
    assert (day_dir.parent.parent / "kept" / "2024" / "a.HEIC").exists()
    assert "2026-10-20\t0\t0" in ctl_run(client, ["trash", "list"])[1]
    rc, out = ctl_run(client, ["trash", "delete", "旭刚", "2026-10-20"])
    assert rc == 0 and out.startswith("deleted ")
    assert "2026-10-20" not in ctl_run(client, ["trash", "list"])[1]
    assert ctl_run(client, ["trash", "delete", "旭刚", "2026-10-20"])[0] == 1  # already gone
    rc, out = ctl_run(client, ["trash", "day", "旭刚", "not-a-day"])
    assert rc == 1 and out.startswith("error: ")


def test_rows_show_utc_times_in_local_tz(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Shanghai")
    time.tzset()
    try:
        assert ctl._fmt(datetime(2026, 10, 21, 9, 12, tzinfo=UTC)) == "2026-10-21 17:12"
        assert ctl._fmt(ctl._ts("2026-10-21T09:12:00+00:00")) == "2026-10-21 17:12"
        assert ctl._fmt(ctl._ts("2026-10-21T09:12:00")) == "2026-10-21 17:12"  # naive = UTC
        assert ctl._fmt(None) == "-" and ctl._ts(None) is None
    finally:
        monkeypatch.undo()
        time.tzset()


class _FakeStream:
    """Stands in for EventStream: replays SSE frames, then times out."""

    def __init__(self, frames: list[tuple[str, dict[str, Any]]]) -> None:
        self.frames = list(frames)

    def __enter__(self) -> _FakeStream:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, deadline: float) -> tuple[str, dict[str, Any]]:
        if not self.frames:
            raise TimeoutError
        return self.frames.pop(0)

    def wait_for(self, deadline: float, match: Any) -> tuple[str, dict[str, Any]]:
        while True:
            topic, data = self.get(deadline)
            if match(topic, data):
                return topic, data


def _frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    return list(ctl_follow.parse_sse(text.splitlines(keepends=True)))


def test_followers_survive_a_minimal_run_finished(monkeypatch: pytest.MonkeyPatch) -> None:
    """An internal-failure frame without exit_code ends the follow with 1, not a KeyError."""
    sse = (
        'event: run.started\ndata: {"run_id": "r1", "library_id": "L", "trigger": "manual",'
        ' "account_id": "A"}\n\n'
        'event: run.finished\ndata: {"run_id": "r1", "status": "failed", "library_id": null}\n\n'
    )
    out = io.StringIO()
    monkeypatch.setattr(ctl_follow, "EventStream", lambda api: _FakeStream(_frames(sse)))
    assert ctl_follow.follow_run(None, "L", lambda: None, out) == 1  # type: ignore[arg-type]
    assert ctl_follow.follow_auth(None, lambda: "r1", lambda p: "", out) == 1  # type: ignore[arg-type]
    assert "login failed with exit code 1" in out.getvalue()


def test_discovery_wait_survives_a_minimal_run_finished() -> None:
    sse = 'event: run.finished\ndata: {"run_id": "r2", "mode": "list", "account_id": "A"}\n\n'
    out = io.StringIO()
    stream = _FakeStream(_frames(sse))
    assert ctl_follow._await_discovery(None, stream, "A", out) == 1  # type: ignore[arg-type]
    assert "exit code 1" in out.getvalue()
