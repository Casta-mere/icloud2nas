from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from icloud2nas.orchestrator import onboarding as onboarding_mod
from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.onboarding import Onboarding, safe_dir_name
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.scheduler import Scheduler
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.secrets import Secrets


@pytest.fixture
def onboarding(config: Config) -> tuple[Onboarding, Store, list[tuple[str, dict[str, Any]]]]:
    engine = make_engine(config.db_path)
    upgrade_db(engine)
    store = Store(engine)
    bus = EventBus()
    seen: list[tuple[str, dict[str, Any]]] = []
    bus.subscribe(lambda t, p: seen.append((t, p)))
    secrets = Secrets.from_config(config)
    runs = RunManager(config, store, secrets, bus)
    return Onboarding(config, store, secrets, bus, runs, Settings(store)), store, seen


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("小明", "小明"),
        (" a/b\\c ", "a_b_c"),
        ("..hidden", "hidden"),
        ("", "fallbk"),
        ("...", "fallbk"),
    ],
)
def test_safe_dir_name(name: str, expected: str) -> None:
    assert safe_dir_name(name, "fallbk") == expected


def test_create_account_encrypts_and_prepares_cookie_dir(
    onboarding: tuple[Onboarding, Store, list[Any]], config: Config
) -> None:
    ob, store, _ = onboarding
    account_id = ob.create_account(
        display_name="小明", apple_id="a@icloud.com", region="cn", password="s3cret"
    )
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None and acc.status == "new" and len(acc.client_id) == 36
        assert acc.password_enc != b"s3cret" and ob.password_for(acc) == "s3cret"
    assert config.cookie_dir(account_id).is_dir()


async def test_auth_with_code_relay_then_library_discovery(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, store, seen = onboarding
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({
            "auth": {"events": [{"ev": "started"}, {"ev": "need_mfa"}, {"wait_code": "123456"},
                                {"ev": "done", "stats": {}}], "exit": 0},
            "list": {"events": [{"ev": "started"},
                                {"ev": "libraries", "personal": ["PrimarySync"],
                                 "shared": ["SharedSync-9"]},
                                {"ev": "done", "stats": {"personal": 1, "shared": 1}}], "exit": 0},
        }),
    )
    account_id = ob.create_account(
        display_name="小明", apple_id="a@icloud.com", region="cn", password="pw"
    )
    session = await ob.start_auth(account_id)
    with pytest.raises(ValueError):
        await ob.start_auth(account_id)  # one at a time
    await asyncio.wait_for(session.prompted.wait(), 5)
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None and acc.status == "awaiting_code"
    await ob.submit_code(account_id, "000000")
    await ob.submit_code(account_id, "123456")
    result = await ob.wait_auth(account_id)
    assert result.exit_code == 0 and account_id not in ob.sessions
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None and acc.status == "ok" and acc.last_auth_ok_at is not None
    personal, shared = await ob.refresh_libraries(account_id)
    assert personal == ["PrimarySync"] and shared == ["SharedSync-9"]
    with store.session() as s:
        libs = {lib.zone_name: lib for lib in repo.list_libraries(s)}
        assert libs["PrimarySync"].enabled is True and libs["PrimarySync"].target_dir == "小明"
        assert libs["PrimarySync"].interval_min == 30 and libs["PrimarySync"].nightly_at == "03:00"
        assert libs["SharedSync-9"].enabled is False and libs["SharedSync-9"].kind == "shared"
        assert libs["SharedSync-9"].target_dir == "shared/SharedSync-9"
    assert ("account.status", {"account_id": account_id, "status": "awaiting_code"}) in seen
    assert ("account.status", {"account_id": account_id, "status": "ok"}) in seen


async def test_auth_without_prompt_and_password_rejection(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, store, _ = onboarding
    account_id = ob.create_account(
        display_name="x", apple_id="b@icloud.com", region="com", password="pw"
    )
    session = await ob.start_auth(account_id)  # default scenario: no need_mfa
    result = await ob.wait_auth(account_id)
    assert result.exit_code == 0 and not session.prompted.is_set()
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"auth": {"events": [{"ev": "started"},
                                        {"ev": "error", "kind": "password", "message": "bad"}],
                         "exit": 4}}),
    )
    await ob.start_auth(account_id, trigger="reauth")
    result = await ob.wait_auth(account_id)
    assert result.exit_code == 4
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None and acc.status == "password_rejected"
    ob.set_password(account_id, "new-pw")
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None and acc.status == "new" and ob.password_for(acc) == "new-pw"


async def test_refresh_libraries_failure_raises(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, _, _ = onboarding
    account_id = ob.create_account(
        display_name="x", apple_id="c@icloud.com", region="cn", password="pw"
    )
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"list": {"events": [{"ev": "started"},
                                        {"ev": "error", "kind": "not_activated",
                                         "message": "indexing"}], "exit": 1}}),
    )
    with pytest.raises(RuntimeError, match="not_activated"):
        await ob.refresh_libraries(account_id)


async def test_refresh_keeps_a_user_disable_and_existing_dir(
    onboarding: tuple[Onboarding, Store, list[Any]],
) -> None:
    ob, store, _ = onboarding
    account_id = ob.create_account(
        display_name="小明", apple_id="d@icloud.com", region="cn", password="pw"
    )
    await ob.refresh_libraries(account_id)  # default list scenario: PrimarySync only
    with store.session() as s:
        lib = repo.list_libraries(s)[0]
        lib.enabled = False
        lib.target_dir = "custom-dir"
    await ob.refresh_libraries(account_id)
    with store.session() as s:
        lib = repo.list_libraries(s)[0]
        assert lib.enabled is False and lib.target_dir == "custom-dir"


async def test_auth_session_never_wedges_when_bookkeeping_fails(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, _, _ = onboarding
    account_id = ob.create_account(
        display_name="x", apple_id="e@icloud.com", region="cn", password="pw"
    )

    def broken(run_id: str, result: Any) -> Any:
        raise RuntimeError("bookkeeping bug")

    monkeypatch.setattr(ob.runs, "finish_run", broken)
    await ob.start_auth(account_id)
    with pytest.raises(RuntimeError, match="bookkeeping"):
        await asyncio.wait_for(ob.wait_auth(account_id), 10)
    assert account_id not in ob.sessions
    monkeypatch.undo()
    session = await ob.start_auth(account_id)  # not blocked any more
    assert (await ob.wait_auth(account_id)).exit_code == 0 and session.task is not None


async def test_start_auth_spawn_failure_fails_the_run(
    onboarding: tuple[Onboarding, Store, list[Any]], config: Config
) -> None:
    ob, store, _ = onboarding
    account_id = ob.create_account(
        display_name="x", apple_id="f@icloud.com", region="cn", password="pw"
    )
    broken_config = Config(
        data_dir=config.data_dir, photos_dir=config.photos_dir,
        worker_cmd=("/definitely/not/a/binary",), tz=config.tz,
    )
    ob.config = broken_config
    with pytest.raises(FileNotFoundError):
        await ob.start_auth(account_id)
    assert account_id not in ob.sessions
    with store.session() as s:
        run = repo.list_runs(s)[0]
        assert run.status == "failed" and run.error_kind == "spawn"


async def test_discard_account_undoes_create_after_a_failed_start(
    onboarding: tuple[Onboarding, Store, list[Any]], config: Config
) -> None:
    ob, store, _ = onboarding
    photos_dir = config.photos_dir / "小明"
    photos_dir.mkdir()
    (photos_dir / "IMG_0001.JPG").write_bytes(b"jpeg")
    account_id = ob.create_account(
        display_name="小明", apple_id="a@icloud.com", region="cn", password="pw",
        target_dir="小明",
    )
    ob.config = Config(
        data_dir=config.data_dir, photos_dir=config.photos_dir,
        worker_cmd=("/definitely/not/a/binary",), tz=config.tz,
    )
    with pytest.raises(FileNotFoundError):
        await ob.start_auth(account_id)
    with store.session() as s:
        run_id = repo.list_runs(s)[0].id
    assert (config.logs_dir / f"{run_id}.log").exists()  # opened before the spawn failed

    ob.discard_account(account_id)

    with store.session() as s:
        assert repo.list_accounts(s) == [] and repo.list_runs(s) == []
    assert not (config.accounts_dir / account_id).exists()
    assert not config.account_plans_dir(account_id).exists()
    assert not (config.logs_dir / f"{run_id}.log").exists()
    assert (photos_dir / "IMG_0001.JPG").read_bytes() == b"jpeg"  # the photos root is not ours


async def test_discard_account_file_cleanup_is_best_effort(
    onboarding: tuple[Onboarding, Store, list[Any]],
    config: Config,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ob, store, _ = onboarding
    account_id = ob.create_account(
        display_name="小明", apple_id="a@icloud.com", region="cn", password="pw"
    )
    ob.config = Config(
        data_dir=config.data_dir, photos_dir=config.photos_dir,
        worker_cmd=("/definitely/not/a/binary",), tz=config.tz,
    )
    with pytest.raises(FileNotFoundError):
        await ob.start_auth(account_id)
    with store.session() as s:
        run_log = config.logs_dir / f"{repo.list_runs(s)[0].id}.log"
    assert config.account_plans_dir(account_id).is_dir()  # prepare_spawn made it
    real_unlink = Path.unlink

    def denied(self: Path, missing_ok: bool = False) -> None:
        if self == run_log:  # say the logs dir lost its search permission
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", denied)

    ob.discard_account(account_id)  # returns normally

    with store.session() as s:
        assert repo.list_accounts(s) == [] and repo.list_runs(s) == []
    assert not (config.accounts_dir / account_id).exists()
    assert not config.account_plans_dir(account_id).exists()
    assert run_log.exists()
    assert f"could not remove {run_log}" in caplog.text


async def test_discard_account_refuses_once_the_login_started(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, store, _ = onboarding
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"auth": {"events": [{"ev": "started"}, {"ev": "need_mfa"},
                                        {"wait_code": "123456"}, {"ev": "done", "stats": {}}],
                             "exit": 0}}),
    )
    account_id = ob.create_account(
        display_name="小明", apple_id="a@icloud.com", region="cn", password="pw"
    )
    session = await ob.start_auth(account_id)
    await asyncio.wait_for(session.prompted.wait(), 5)
    with pytest.raises(RuntimeError):
        ob.discard_account(account_id)
    await ob.submit_code(account_id, "123456")
    assert (await ob.wait_auth(account_id)).exit_code == 0
    with store.session() as s:
        assert [a.id for a in repo.list_accounts(s)] == [account_id]


async def test_submit_code_after_finish_raises(
    onboarding: tuple[Onboarding, Store, list[Any]],
) -> None:
    ob, _, _ = onboarding
    account_id = ob.create_account(
        display_name="x", apple_id="g@icloud.com", region="cn", password="pw"
    )
    session = await ob.start_auth(account_id)  # default scenario finishes without a prompt
    await asyncio.wait_for(session.finished.wait(), 10)
    with pytest.raises((RuntimeError, KeyError)):
        await ob.submit_code(account_id, "123456")


async def test_second_account_with_same_shared_zone_keeps_personal_library(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, store, _ = onboarding
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO",
        json.dumps({"list": {"events": [{"ev": "started"},
                                        {"ev": "libraries", "personal": ["PrimarySync"],
                                         "shared": ["SharedSync-9"]},
                                        {"ev": "done", "stats": {}}], "exit": 0}}),
    )
    first = ob.create_account(display_name="小明", apple_id="h@icloud.com", region="cn",
                              password="pw")
    second = ob.create_account(display_name="小红", apple_id="i@icloud.com", region="cn",
                               password="pw")
    await ob.refresh_libraries(first)
    personal, shared = await ob.refresh_libraries(second)
    assert personal == ["PrimarySync"] and shared == ["SharedSync-9"]
    await ob.refresh_libraries(second)  # idempotent, never raises
    with store.session() as s:
        libs = repo.list_libraries(s)
        mine = {lib.account_id for lib in libs if lib.kind == "personal" and lib.enabled}
        assert mine == {first, second}
        shared_rows = [lib for lib in libs if lib.kind == "shared"]
        assert len(shared_rows) == 1 and shared_rows[0].account_id == first


def test_personal_dir_named_shared_is_rejected(
    onboarding: tuple[Onboarding, Store, list[Any]],
) -> None:
    _, store, _ = onboarding
    with store.session() as s:
        with pytest.raises(ValueError, match="shared"):
            repo.validate_target_dir(s, "shared/x")
        assert repo.validate_target_dir(s, "shared/x", kind="shared") == "shared/x"


def test_set_password_publishes_account_updated(
    onboarding: tuple[Onboarding, Store, list[Any]],
) -> None:
    ob, _, seen = onboarding
    account_id = ob.create_account(
        display_name="小明", apple_id="a@icloud.com", region="cn", password="old"
    )
    ob.set_password(account_id, "new")
    assert seen[-1] == ("account.updated", {"account_id": account_id})


async def test_create_account_with_target_dir_feeds_refresh(
    onboarding: tuple[Onboarding, Store, list[Any]],
) -> None:
    ob, store, _ = onboarding
    with pytest.raises(ValueError):
        ob.create_account(
            display_name="x", apple_id="z@icloud.com", region="cn", password="pw",
            target_dir="../escape",
        )
    with store.session() as s:
        assert repo.get_account_by_apple_id(s, "z@icloud.com") is None
    account_id = ob.create_account(
        display_name="小明", apple_id="a@icloud.com", region="cn", password="pw",
        target_dir="家/小明",
    )
    await ob.refresh_libraries(account_id)
    with store.session() as s:
        assert [lib.target_dir for lib in repo.list_libraries(s)] == ["家/小明"]


# -- renewal (立即续期): only success or a rejected password change the account ---------------


def _ready_account(ob: Onboarding, store: Store, apple_id: str = "r@icloud.com") -> str:
    """An account that syncs: status ok, one enabled personal library, an older error text."""
    account_id = ob.create_account(
        display_name="小明", apple_id=apple_id, region="cn", password="pw"
    )
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None
        acc.status = "ok"
        acc.last_error = "an older error"
        library = repo.upsert_library(
            s, acc, kind="personal", zone_name="PrimarySync", display_name="小明",
            target_dir="小明", interval_min=30, nightly_at="03:00",
        )
        library.enabled = True
    return account_id


def _scenario(monkeypatch: pytest.MonkeyPatch, events: list[dict[str, Any]], code: int) -> None:
    monkeypatch.setenv(
        "FAKE_WORKER_SCENARIO", json.dumps({"auth": {"events": events, "exit": code}})
    )


def _assert_untouched(store: Store, account_id: str) -> None:
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None
        assert acc.status == "ok" and acc.failures == 0 and acc.backoff_until is None
        assert acc.last_error == "an older error"


ASKED = [{"ev": "started"}, {"ev": "need_mfa"}]


@pytest.mark.parametrize(
    ("events", "code", "kind"),
    [
        (
            [*ASKED, {"ev": "error", "kind": "mfa_timeout", "message": "no code within 600s"}],
            1,
            "mfa_timeout",
        ),
        ([*ASKED, {"ev": "error", "kind": "network", "message": "reset"}], 5, "network"),
        ([{"ev": "started"}, {"ev": "error", "kind": "unavailable", "message": "503"}], 5,
         "unavailable"),
        (ASKED, 1, "no terminal event"),
    ],
)
async def test_failed_renewal_leaves_the_account_syncing(
    onboarding: tuple[Onboarding, Store, list[Any]],
    monkeypatch: pytest.MonkeyPatch,
    config: Config,
    events: list[dict[str, Any]],
    code: int,
    kind: str,
) -> None:
    ob, store, seen = onboarding
    account_id = _ready_account(ob, store)
    _scenario(monkeypatch, events, code)
    session = await ob.start_auth(account_id, trigger="reauth", renew=True)
    result = await ob.wait_auth(account_id)
    assert result.exit_code == code
    _assert_untouched(store, account_id)
    with store.session() as s:
        run = repo.get_run(s, session.run_id)
        assert run is not None and run.status == "failed" and run.error_kind == kind
    finished = [p for t, p in seen if t == "run.finished"][-1]
    assert finished["status"] == "failed" and finished["account_failures"] == 0  # no notify
    if any(e.get("ev") == "need_mfa" for e in events):
        statuses = [p["status"] for t, p in seen if t == "account.status"]
        assert statuses[-2:] == ["awaiting_code", "ok"]  # put back once the renewal gave up
        position = {t: i for i, (t, _p) in enumerate(seen)}
        assert position["account.status"] < position["run.finished"]
    scheduler = Scheduler(
        config, store, ob.secrets, ob.bus, ob.runs, ob, ob.settings, jitter_s=(0, 0)
    )
    assert [job.account_id for job in scheduler.due_jobs()] == [account_id]


async def test_killed_renewal_leaves_the_account_syncing(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, store, _ = onboarding
    account_id = _ready_account(ob, store)
    _scenario(monkeypatch, [*ASKED, {"sleep": 30}], 0)
    monkeypatch.setattr(onboarding_mod, "MFA_TIMEOUT_S", -59.5)  # wait() gives up after 0.5 s
    session = await ob.start_auth(account_id, trigger="reauth", renew=True)
    result = await asyncio.wait_for(ob.wait_auth(account_id), 15)
    assert result.killed and result.killed_by == "timeout"
    _assert_untouched(store, account_id)
    with store.session() as s:
        run = repo.get_run(s, session.run_id)
        assert run is not None and run.status == "failed" and run.error_kind == "killed"


async def test_renewal_with_a_rejected_password_marks_it(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, store, _ = onboarding
    account_id = _ready_account(ob, store)
    _scenario(monkeypatch, [{"ev": "started"}, {"ev": "error", "kind": "password",
                                                "message": "bad"}], 4)
    await ob.start_auth(account_id, trigger="reauth", renew=True)
    assert (await ob.wait_auth(account_id)).exit_code == 4
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None and acc.status == "password_rejected" and acc.last_error == "bad"


async def test_successful_renewal_updates_the_account(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    ob, store, _ = onboarding
    account_id = _ready_account(ob, store)
    _scenario(
        monkeypatch,
        [*ASKED, {"wait_code": "123456"}, {"ev": "mfa_result", "ok": True},
         {"ev": "session", "trust_expires": "2027-01-19T09:12:13+00:00"},
         {"ev": "done", "stats": {}}],
        0,
    )
    await ob.start_auth(account_id, trigger="reauth", renew=True)
    await ob.submit_code(account_id, "123456")
    assert (await ob.wait_auth(account_id)).exit_code == 0
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None and acc.status == "ok" and acc.last_error is None
        assert acc.session_expires_at is not None and acc.session_expires_at.year == 2027


async def test_renewal_spawn_failure_leaves_the_account_alone(
    onboarding: tuple[Onboarding, Store, list[Any]], config: Config
) -> None:
    ob, store, _ = onboarding
    account_id = _ready_account(ob, store)
    ob.config = Config(
        data_dir=config.data_dir, photos_dir=config.photos_dir,
        worker_cmd=("/definitely/not/a/binary",), tz=config.tz,
    )
    with pytest.raises(FileNotFoundError):
        await ob.start_auth(account_id, trigger="reauth", renew=True)
    assert account_id not in ob.sessions
    _assert_untouched(store, account_id)
    with store.session() as s:
        run = repo.list_runs(s)[0]
        assert run.status == "failed" and run.error_kind == "spawn"


async def test_a_plain_reauth_that_times_out_still_backs_off(
    onboarding: tuple[Onboarding, Store, list[Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged for 重新验证: the account really needs a code then."""
    ob, store, _ = onboarding
    account_id = _ready_account(ob, store)
    _scenario(
        monkeypatch,
        [*ASKED, {"ev": "error", "kind": "mfa_timeout", "message": "no code within 600s"}],
        1,
    )
    await ob.start_auth(account_id, trigger="reauth")
    await ob.wait_auth(account_id)
    with store.session() as s:
        acc = repo.get_account(s, account_id)
        assert acc is not None and acc.failures == 1 and acc.backoff_until is not None
