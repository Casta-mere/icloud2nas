from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.onboarding import Onboarding, safe_dir_name
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, init_db, make_engine
from icloud2nas.orchestrator.store.secrets import Secrets


@pytest.fixture
def onboarding(config: Config) -> tuple[Onboarding, Store, list[tuple[str, dict[str, Any]]]]:
    engine = make_engine(config.db_path)
    init_db(engine)
    store = Store(engine)
    bus = EventBus()
    seen: list[tuple[str, dict[str, Any]]] = []
    bus.subscribe(lambda t, p: seen.append((t, p)))
    secrets = Secrets.from_config(config)
    runs = RunManager(config, store, secrets, bus)
    return Onboarding(config, store, secrets, bus, runs), store, seen


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("旭刚", "旭刚"),
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
        display_name="旭刚", apple_id="a@icloud.com", region="cn", password="s3cret"
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
        display_name="旭刚", apple_id="a@icloud.com", region="cn", password="pw"
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
        assert libs["PrimarySync"].enabled is True and libs["PrimarySync"].target_dir == "旭刚"
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
