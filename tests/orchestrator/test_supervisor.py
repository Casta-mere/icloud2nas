from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
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
    started = asyncio.Event()
    proc = WorkerProcess(
        config, run_id="t1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "t1.log",
        on_event=lambda e: started.set() if e["ev"] == "started" else None,
    )
    await proc.start()
    await asyncio.wait_for(started.wait(), 20)  # the timeout below starts once it is running
    result = await proc.wait(timeout_s=0.5)
    assert result.killed and result.exit_code != 0 and result.terminal is None
    assert result.killed_by == "timeout"
    assert [e["ev"] for e in result.events] == ["started"]


async def test_oversized_stdout_line_kills_worker(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scenario(
        monkeypatch,
        sync={"events": [{"ev": "started"}, {"raw_len": 17 * 1024 * 1024}, {"sleep": 30}],
              "exit": 0},
    )
    result = await run_worker(
        config, run_id="o1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "o1.log", timeout_s=20,
    )
    assert result.killed and result.exit_code != 0 and result.killed_by == "protocol"
    assert [e["ev"] for e in result.events] == ["started"]


async def test_reconcile_plan_then_done_terminal_is_done(config: Config) -> None:
    result = await run_worker(
        config, run_id="rc1", mode="reconcile", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "rc1.log",
    )
    assert result.terminal is not None and result.terminal["ev"] == "done"
    assert any(e["ev"] == "reconcile_plan" for e in result.events)


async def test_start_failure_closes_log_handle(config: Config) -> None:
    broken = Config(
        data_dir=config.data_dir, photos_dir=config.photos_dir,
        worker_cmd=("/definitely/not/a/binary",), tz=config.tz,
    )
    proc = WorkerProcess(
        broken, run_id="x1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "x1.log",
    )
    with pytest.raises(FileNotFoundError):
        await proc.start()
    assert proc._log_file is None  # noqa: SLF001 - lifecycle check


async def test_wait_is_idempotent_and_send_code_after_close_raises(config: Config) -> None:
    proc = WorkerProcess(
        config, run_id="w1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "w1.log",
    )
    await proc.start()
    with pytest.raises(RuntimeError, match="stdin is closed"):
        await proc.send_code("123456")
    first = await proc.wait(timeout_s=10)
    second = await proc.wait(timeout_s=10)
    assert first is second and first.exit_code == 0


class _Spawned(Exception):
    """Raised by the fake create_subprocess_exec once it has recorded its kwargs."""


async def _spawn_kwargs(
    config: Config, monkeypatch: pytest.MonkeyPatch, owner: tuple[int, int] | None
) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def fake_exec(*argv: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise _Spawned

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    proc = WorkerProcess(
        config, run_id="u1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "u1.log", owner=owner,
    )
    with pytest.raises(_Spawned):
        await proc.start()
    return seen


async def test_worker_spawned_as_owner(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    kwargs = await _spawn_kwargs(config, monkeypatch, (1000, 10))
    assert kwargs["user"] == 1000 and kwargs["group"] == 10 and kwargs["extra_groups"] == []
    kwargs = await _spawn_kwargs(config, monkeypatch, None)
    assert not {"user", "group", "extra_groups"} & set(kwargs)


@pytest.mark.skipif(os.geteuid() != 0, reason="switching uid needs root")
async def test_worker_really_switches_uid(config: Config) -> None:
    shared = Path(tempfile.mkdtemp(prefix="i2n-uid-"))  # pytest's tmp_path is 0700 root-only
    shared.chmod(0o755)
    script = shared / "whoami-worker.sh"
    script.write_text(
        '#!/bin/sh\nprintf \'{"ev":"done","stats":{"uid":%s,"gid":%s,"groups":"%s"}}\\n\' '
        '"$(id -u)" "$(id -g)" "$(id -G)"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    nobody = Config(
        data_dir=config.data_dir, photos_dir=config.photos_dir, worker_cmd=(str(script),),
        tz=config.tz,
    )
    try:
        result = await run_worker(
            nobody, run_id="id1", mode="sync", job=JOB, password=None, interactive=False,
            log_path=config.logs_dir / "id1.log", owner=(65534, 65534), timeout_s=10,
        )
    finally:
        shutil.rmtree(shared)
    assert result.exit_code == 0 and result.terminal is not None
    assert result.terminal["stats"] == {"uid": 65534, "gid": 65534, "groups": "65534"}
    assert (config.logs_dir / "id1.log").stat().st_uid == 0  # the run log stays ours


async def test_worker_env_is_an_allowlist(
    config: Config, calls_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("I2N_SECRET_KEY", "fernet-key-for-every-password")
    monkeypatch.setenv("SOME_SECRET", "hunter2")
    monkeypatch.setenv("LC_ALL", "C.UTF-8")
    await run_worker(
        config, run_id="e1", mode="sync", job=JOB, password="pw", interactive=False,
        log_path=config.logs_dir / "e1.log",
    )
    env = json.loads(calls_file.read_text(encoding="utf-8"))["env"]
    assert "I2N_SECRET_KEY" not in env and "SOME_SECRET" not in env
    assert {"PATH", "TZ", "LC_ALL", "I2N_JOB", "I2N_PASSWORD", "FAKE_WORKER_CALLS"} <= set(env)
    assert env["HOME"] == str(config.accounts_dir / "acc1")
