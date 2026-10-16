from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.supervisor import WorkerProcess, run_worker

JOB = {
    "account_id": "acc1",
    "apple_id": "a@icloud.com",
    "region": "cn",
    "client_id": "11111111-1111-1111-1111-111111111111",
    "cookie_dir": "/tmp/cookies",
    "photos_root": "/photos",
    "target_dir": "/photos/旭刚",
    "plan_dir": "/data/plans",
    "interactive": False,
    "full": False,
}


@pytest.fixture
def calls_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_WORKER_CALLS", str(path))
    return path


def _scenario(monkeypatch: pytest.MonkeyPatch, **modes: dict[str, Any]) -> None:
    monkeypatch.setenv("FAKE_WORKER_SCENARIO", json.dumps(modes))


async def test_sync_run_passes_job_and_password_and_collects_events(
    config: Config, calls_file: Path
) -> None:
    log = config.logs_dir / "r1.log"
    seen: list[str] = []
    result = await run_worker(
        config, run_id="r1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=log, on_event=lambda e: seen.append(e["ev"]),
    )
    assert result.exit_code == 0 and not result.killed
    assert [e["ev"] for e in result.events] == ["started", "done"] == seen
    assert result.terminal is not None and result.terminal["ev"] == "done"
    assert result.terminal["stats"] == {"downloaded": 1, "trashed": 0, "full": False}
    call = json.loads(calls_file.read_text(encoding="utf-8"))
    assert call["argv"] == ["--mode", "sync", "--run-id", "r1"]
    assert call["job"]["target_dir"] == "/photos/旭刚" and call["has_password"] is True
    assert log.exists()


async def test_interactive_auth_relays_codes(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(
        monkeypatch,
        auth={
            "events": [
                {"ev": "started"},
                {"ev": "need_mfa"},
                {"wait_code": "123456"},
                {"ev": "done", "stats": {}},
            ],
            "exit": 0,
        },
    )
    got_prompt = asyncio.Event()

    def on_event(event: dict[str, Any]) -> None:
        if event["ev"] == "need_mfa":
            got_prompt.set()

    proc = WorkerProcess(
        config, run_id="a1", mode="auth", job={**JOB, "interactive": True}, password="pw",
        interactive=True, log_path=config.logs_dir / "a1.log", on_event=on_event,
    )
    await proc.start()
    assert proc.pid is not None
    await asyncio.wait_for(got_prompt.wait(), 5)
    await proc.send_code("000000")
    await proc.send_code("123456")
    result = await proc.wait(timeout_s=10)
    assert result.exit_code == 0
    evs = [e["ev"] for e in result.events]
    assert evs == ["started", "need_mfa", "mfa_result", "mfa_result", "done"]
    assert result.events[2]["ok"] is False and result.events[3]["ok"] is True


async def test_apply_plan_argv_and_no_password(config: Config, calls_file: Path) -> None:
    await run_worker(
        config, run_id="p1", mode="reconcile", job=JOB, password=None, interactive=False,
        log_path=config.logs_dir / "p1.log", apply_plan="/data/plans/x.txt",
    )
    call = json.loads(calls_file.read_text(encoding="utf-8"))
    assert call["argv"] == [
        "--mode", "reconcile", "--run-id", "p1", "--apply-plan", "/data/plans/x.txt",
    ]
    assert call["has_password"] is False


async def test_crash_without_events_and_garbage_lines(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(monkeypatch, sync={"events": [{"raw": "not json"}, {"stderr": "boom"}], "exit": 1})
    result = await run_worker(
        config, run_id="c1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "c1.log",
    )
    assert result.exit_code == 1 and result.events == [] and result.terminal is None
    assert "boom" in (config.logs_dir / "c1.log").read_text(encoding="utf-8")


async def test_timeout_kills_the_worker(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    _scenario(monkeypatch, sync={"events": [{"ev": "started"}, {"sleep": 30}], "exit": 0})
    proc = WorkerProcess(
        config, run_id="t1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "t1.log",
    )
    await proc.start()
    result = await proc.wait(timeout_s=0.5)
    assert result.killed and result.exit_code != 0 and result.terminal is None
    assert [e["ev"] for e in result.events] == ["started"]


async def test_reconcile_plan_then_done_terminal_is_done(config: Config) -> None:
    result = await run_worker(
        config, run_id="rc1", mode="reconcile", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "rc1.log",
    )
    assert result.terminal is not None and result.terminal["ev"] == "done"
    assert any(e["ev"] == "reconcile_plan" for e in result.events)
