from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.updates import (
    UpdateChecker,
    UpdateResult,
    compare_url,
    newest_release,
    parse_release,
)
from tests.fake_registry import FakeRegistry

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
COMPARE = "https://github.com/Casta-mere/icloud2nas/compare/"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Env:
    def __init__(self, config: Config) -> None:
        engine = make_engine(config.db_path)
        upgrade_db(engine)
        self.settings = Settings(Store(engine))
        self.settings.set("access_mode", "lan")
        self.bus = EventBus()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.bus.subscribe(lambda topic, payload: self.events.append((topic, payload)))
        self.registry = FakeRegistry(tags=["0.1.1", "0.2.0"])
        self.clock = Clock(T0)

    def checker(self, current: str = "0.1.1", **kw: Any) -> UpdateChecker:
        kw.setdefault("transport", self.registry.transport())
        return UpdateChecker(self.settings, self.bus, current=current, clock=self.clock, **kw)


@pytest.fixture
def env(config: Config) -> Env:
    return Env(config)


async def until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.01)


@contextlib.asynccontextmanager
async def running(checker: UpdateChecker) -> AsyncIterator[asyncio.Event]:
    stop = asyncio.Event()
    task = asyncio.create_task(checker.run_forever(stop))
    try:
        yield stop
    finally:
        stop.set()
        checker.cancel()
        await asyncio.wait_for(task, 2)


def test_parse_release() -> None:
    assert parse_release("0.10.0") == (0, 10, 0)
    assert parse_release("12.0.3") == (12, 0, 3)
    for tag in ("v1.2.3", "1.2", "1.2.3-rc.1", "1.2.3.4", "latest", "1.2.3\n", " 1.2.3", ""):
        assert parse_release(tag) is None, tag


def test_newest_release_ignores_non_release_tags() -> None:
    tags = [
        "main", "sha-e80af31", "0.1.1", "0.1", "latest", "v9.9.9", "0.2.0-rc.1", "1.0",
        "9.9.9\n", "0.10.0", "0.9.0",
    ]
    assert newest_release(tags) == "0.10.0"  # numeric, not string order, and never the noise
    assert newest_release(["main", "latest"]) is None
    assert newest_release([]) is None


def test_compare_url() -> None:
    assert compare_url("0.1.1", "0.2.0") == f"{COMPARE}v0.1.1...v0.2.0"
    assert compare_url("0.0.0+dev", "0.2.0") is None
    assert compare_url("dev", "0.2.0") is None


def test_result_to_json() -> None:
    empty = UpdateResult(None, False, None, None, None)
    assert empty.to_json() == {
        "latest": None, "update_available": False, "checked_at": None, "error": None,
        "compare_url": None,
    }
    found = UpdateResult("0.2.0", True, T0, None, f"{COMPARE}v0.1.1...v0.2.0")
    assert found.to_json()["checked_at"] == "2026-11-04T08:00:00+00:00"


def test_update_check_setting_defaults_on_and_seeds(env: Env, config: Config) -> None:
    assert env.settings.get("update_check") is True
    assert env.settings.all()["update_check"] is True
    fresh = make_engine(config.data_dir / "fresh.db")
    upgrade_db(fresh)
    seeded = Settings(Store(fresh))
    seeded.seed_from_env({"I2N_UPDATE_CHECK": "0"})
    assert seeded.get("update_check") is False
    env.settings.set("update_check", "1")  # saved by the owner: the env seed no longer applies
    env.settings.seed_from_env({"I2N_UPDATE_CHECK": "0"})
    assert env.settings.get("update_check") is True
    fresh.dispose()


async def test_before_any_check_there_is_no_result(env: Env) -> None:
    checker = env.checker()
    assert checker.result == UpdateResult(None, False, None, None, None)
    assert env.registry.requests == []


async def test_update_available(env: Env) -> None:
    r = await env.checker(current="0.1.1").check()
    assert (r.latest, r.update_available, r.error) == ("0.2.0", True, None)
    assert r.compare_url == f"{COMPARE}v0.1.1...v0.2.0"
    assert r.checked_at == T0
    assert env.registry.tag_requests[0].params["n"] == "1000"


@pytest.mark.parametrize("tags", [["0.1.1"], ["0.1.0"], ["latest", "main"]])
async def test_equal_or_older_is_up_to_date(env: Env, tags: list[str]) -> None:
    env.registry.tags = tags
    r = await env.checker(current="0.1.1").check()
    assert (r.update_available, r.compare_url, r.error) == (False, None, None)
    assert r.latest == newest_release(tags)
    assert r.checked_at == T0


async def test_non_release_current_compares_as_zero(env: Env) -> None:
    zero = await env.checker(current="0.0.0").check()
    assert (zero.latest, zero.update_available) == ("0.2.0", True)
    assert zero.compare_url == f"{COMPARE}v0.0.0...v0.2.0"
    dev = await env.checker(current="dev").check()
    assert (dev.latest, dev.update_available, dev.compare_url) == ("0.2.0", True, None)


@pytest.mark.parametrize("omit", [False, True], ids=["tags-null", "no-tags-key"])
async def test_null_tags_means_no_release(env: Env, omit: bool) -> None:
    env.registry.tags = None
    env.registry.omit_tags = omit
    r = await env.checker().check()
    assert (r.latest, r.update_available, r.error, r.compare_url) == (None, False, None, None)
    assert r.checked_at == T0


async def test_follows_path_only_link_pagination(env: Env) -> None:
    env.registry.tags = ["main", "0.1.1", "sha-1", "0.1", "latest", "sha-2", "0.3.0"]
    env.registry.page_size = 2
    r = await env.checker().check()
    assert (r.latest, r.update_available) == ("0.3.0", True)
    pages = env.registry.tag_requests
    assert len(pages) == 4
    assert all(url.host == "ghcr.io" and url.scheme == "https" for url in pages)
    assert [url.params.get("last") for url in pages] == [None, "0.1.1", "0.1", "sha-2"]


async def test_link_to_another_host_is_not_followed(env: Env) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "t"})
        if request.url.host != "ghcr.io":
            raise AssertionError(f"followed a link off ghcr.io: {request.url}")
        link = '<https://evil.example/v2/casta-mere/icloud2nas/tags/list?last=x>; rel="next"'
        return httpx.Response(200, json={"tags": ["0.2.0"]}, headers={"Link": link})

    r = await env.checker(transport=httpx.MockTransport(handler)).check()
    assert (r.latest, r.error) == ("0.2.0", None)


@pytest.mark.parametrize(
    "link",
    [
        "http://ghcr.io/v2/casta-mere/icloud2nas/tags/list?last=x",  # plaintext: token in the clear
        "https://ghcr.io:8443/v2/casta-mere/icloud2nas/tags/list?last=x",  # same host, other port
    ],
)
async def test_link_that_changes_scheme_or_port_is_not_followed(env: Env, link: str) -> None:
    seen: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        if request.url.path == "/token":
            return httpx.Response(200, json={"token": "t"})
        if (request.url.scheme, request.url.port) != ("https", None):
            raise AssertionError(f"followed a link that left https://ghcr.io: {request.url}")
        next_link = {"Link": f'<{link}>; rel="next"'}
        return httpx.Response(200, json={"tags": ["0.2.0"]}, headers=next_link)

    r = await env.checker(transport=httpx.MockTransport(handler)).check()
    assert (r.latest, r.error) == ("0.2.0", None)
    assert len(seen) == 2  # the token and the first page only


async def test_token_403_is_unexpected_response(env: Env) -> None:
    env.registry.token_status = 403
    r = await env.checker().check()
    assert r.error == "unexpected response (HTTP 403)"
    assert (r.latest, r.update_available, r.checked_at) == (None, False, T0)


async def test_connect_error_is_unreachable(env: Env) -> None:
    env.registry.fail = httpx.ConnectError("x")
    r = await env.checker().check()
    assert r.error == "couldn't reach ghcr.io"


async def test_request_timeout_is_timed_out(env: Env) -> None:
    env.registry.fail = httpx.ReadTimeout("x")
    r = await env.checker().check()
    assert r.error == "timed out"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>captive portal</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"no": "token"}),
    ],
    ids=["not-json", "json-list", "no-token"],
)
async def test_malformed_body_is_unexpected_response(env: Env, response: httpx.Response) -> None:
    transport = httpx.MockTransport(lambda request: response)
    r = await env.checker(transport=transport).check()
    assert r.error == "unexpected response"
    assert r.latest is None


async def test_whole_check_is_capped(env: Env) -> None:
    env.registry.delay = asyncio.Event()  # never set: the registry hangs
    loop = asyncio.get_running_loop()
    started = loop.time()
    r = await asyncio.wait_for(env.checker(check_cap_s=0.05).check(), 2)
    assert r.error == "timed out"
    assert loop.time() - started < 1


async def test_failure_keeps_previous_update(env: Env) -> None:
    checker = env.checker()
    first = await checker.check()
    assert (first.latest, first.update_available, first.error) == ("0.2.0", True, None)
    env.clock.now = T0 + timedelta(days=1)
    env.registry.fail = httpx.ConnectError("x")
    failed = await checker.check()
    assert (failed.latest, failed.update_available) == ("0.2.0", True)
    assert failed.compare_url == first.compare_url
    assert failed.error == "couldn't reach ghcr.io"
    assert failed.checked_at == T0 + timedelta(days=1)
    assert checker.result == failed
    env.registry.fail = None
    again = await checker.check()
    assert (again.latest, again.update_available, again.error) == ("0.2.0", True, None)


async def test_publishes_version_checked(env: Env) -> None:
    checker = env.checker()
    await checker.check()
    assert env.events == [("version.checked", {})]
    env.registry.fail = httpx.ConnectError("x")
    await checker.check()
    assert env.events == [("version.checked", {}), ("version.checked", {})]


async def test_concurrent_checks_join(env: Env) -> None:
    env.registry.delay = asyncio.Event()
    checker = env.checker()
    a = asyncio.create_task(checker.check())
    b = asyncio.create_task(checker.check())
    await until(lambda: env.registry.token_requests == 1)
    await asyncio.sleep(0.02)
    env.registry.delay.set()
    ra, rb = await asyncio.gather(a, b)
    assert ra == rb and ra.latest == "0.2.0"
    assert env.registry.token_requests == 1
    assert len(env.events) == 1
    await checker.check()  # a later press starts a new check
    assert env.registry.token_requests == 2


async def test_cancelled_caller_does_not_cancel_the_check(env: Env) -> None:
    env.registry.delay = asyncio.Event()
    checker = env.checker()
    a = asyncio.create_task(checker.check())
    b = asyncio.create_task(checker.check())
    await until(lambda: env.registry.token_requests == 1)
    a.cancel()
    env.registry.delay.set()
    rb = await b
    assert a.cancelled()
    assert rb.latest == "0.2.0" and checker.result == rb


async def test_first_auto_check_waits_for_startup_delay(env: Env) -> None:
    checker = env.checker(startup_delay_s=0.3, interval_s=10)
    async with running(checker):
        await asyncio.sleep(0.1)
        assert env.registry.token_requests == 0
        await until(lambda: env.registry.token_requests == 1)
    assert checker.result.latest == "0.2.0"


async def test_auto_checks_respect_toggle_and_setup(env: Env) -> None:
    env.settings.set("update_check", "0")
    checker = env.checker(startup_delay_s=0.01, interval_s=10)
    async with running(checker):
        await asyncio.sleep(0.1)
        assert env.registry.token_requests == 0  # turned off
        env.settings.set_many({"update_check": "1", "access_mode": ""})
        await asyncio.sleep(0.1)
        assert env.registry.token_requests == 0  # first-run setup: LAN/WAN not chosen yet
        assert checker.result.checked_at is None
        env.settings.set("access_mode", "lan")
        await until(lambda: env.registry.token_requests == 1)
        await asyncio.sleep(0.1)
        assert env.registry.token_requests == 1  # then once per interval
    assert checker.result.latest == "0.2.0"


async def test_toggle_is_read_every_tick(env: Env) -> None:
    checker = env.checker(startup_delay_s=0.01, interval_s=0.05)
    async with running(checker):
        await until(lambda: checker.result.latest == "0.2.0")
        env.settings.set("update_check", "0")
        await asyncio.sleep(0.1)  # let a check that was already running finish
        settled = env.registry.token_requests
        found = checker.result
        await asyncio.sleep(0.25)
        assert env.registry.token_requests == settled  # off: no more automatic checks
        assert checker.result == found  # and the known update is kept
        assert (found.latest, found.update_available) == ("0.2.0", True)
        env.settings.set("update_check", "1")
        await until(lambda: env.registry.token_requests > settled)  # on again, no restart


async def test_button_works_with_auto_checks_off(env: Env) -> None:
    env.settings.set("update_check", "0")
    r = await env.checker().check()
    assert r.latest == "0.2.0"


async def test_loop_survives_an_unexpected_error(env: Env) -> None:
    checker = env.checker(startup_delay_s=0.01, interval_s=0.01)
    calls: list[int] = []
    real_check = checker.check

    async def flaky() -> UpdateResult:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return await real_check()

    checker.check = flaky  # type: ignore[method-assign]
    async with running(checker):
        await until(lambda: checker.result.latest == "0.2.0")
    assert len(calls) >= 2


async def test_stop_ends_the_loop_during_a_check(env: Env) -> None:
    env.registry.delay = asyncio.Event()  # the check hangs until cancelled
    checker = env.checker(startup_delay_s=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(checker.run_forever(stop))
    await until(lambda: env.registry.token_requests == 1)
    stop.set()
    checker.cancel()
    await asyncio.wait_for(task, 1)
    assert task.done() and not task.cancelled()
