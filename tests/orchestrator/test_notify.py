from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.housekeeping import Housekeeping
from icloud2nas.orchestrator.notify import Notifier
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.secrets import Secrets

T0 = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Recorder:
    def __init__(self) -> None:
        self.sent: list[tuple[list[str], str, str]] = []
        self.result = True
        self.boom = False

    def __call__(self, urls: list[str], title: str, body: str) -> bool:
        if self.boom:
            raise RuntimeError("smtp down")
        self.sent.append((urls, title, body))
        return self.result


@pytest.fixture
def nw(config: Config) -> dict[str, Any]:
    engine = make_engine(config.db_path)
    upgrade_db(engine)
    store = Store(engine)
    secrets = Secrets.from_config(config)
    settings = Settings(store)
    settings.set("apprise_urls", "json://a\n\n  json://b  \n")
    bus = EventBus()
    clock = Clock(T0)
    rec = Recorder()
    with store.session() as s:
        acc = repo.create_account(
            s,
            display_name="小明",
            apple_id="a@icloud.com",
            region="cn",
            password_enc=secrets.encrypt("pw"),
            client_id="11111111-1111-1111-1111-111111111111",
        )
        lib = repo.upsert_library(
            s,
            acc,
            kind="personal",
            zone_name="PrimarySync",
            display_name="小明相册",
            target_dir="xugang",
            interval_min=30,
            nightly_at="03:00",
        )
        ids = {"acc": acc.id, "lib": lib.id}
    notifier = Notifier(store, settings, bus, send=rec, clock=clock)
    notifier.start()
    return {
        "store": store,
        "settings": settings,
        "bus": bus,
        "clock": clock,
        "rec": rec,
        "notifier": notifier,
        **ids,
    }


def _six(nw: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [
        ("account.status", {"account_id": nw["acc"], "status": "needs_reauth"}),
        ("account.status", {"account_id": nw["acc"], "status": "password_rejected"}),
        (
            "run.finished",
            {"run_id": "r", "status": "failed", "library_id": nw["lib"], "account_failures": 3},
        ),
        ("plan.pending", {"plan_id": "p", "extraneous": 42, "library_id": nw["lib"]}),
        ("space.low", {"min_free_gb": 20}),
        (
            "run.finished",
            {"run_id": "r", "status": "ok", "library_id": nw["lib"], "first_full": True},
        ),
    ]


def test_decide_maps_every_event(nw: dict[str, Any]) -> None:
    n: Notifier = nw["notifier"]
    events = []
    for topic, payload in _six(nw):
        decision = n.decide(topic, payload)
        assert decision is not None
        events.append(decision[0])
    assert events == [
        "needs_code",
        "password_rejected",
        "repeated_failure",
        "plan_pending",
        "space_low",
        "first_full",
    ]
    assert n.decide("run.started", {}) is None
    assert n.decide("account.status", {"account_id": nw["acc"], "status": "ok"}) is None
    two = {"status": "failed", "library_id": nw["lib"], "account_failures": 2}
    assert n.decide("run.finished", two) is None
    nw["settings"].set("notify.space_low.enabled", "false")
    assert n.decide("space.low", {"min_free_gb": 20}) is None


def test_decide_skips_deleted_subject(nw: dict[str, Any]) -> None:
    n: Notifier = nw["notifier"]
    assert n.decide("account.status", {"account_id": "gone", "status": "needs_reauth"}) is None
    assert n.decide("plan.pending", {"library_id": "gone", "extraneous": 1}) is None


def test_body_contains_name_reason_and_link(nw: dict[str, Any]) -> None:
    n = Notifier(
        nw["store"],
        nw["settings"],
        nw["bus"],
        send=nw["rec"],
        link_fallback=lambda: "http://nas:8080",
    )
    decision = n.decide("account.status", {"account_id": nw["acc"], "status": "needs_reauth"})
    assert decision is not None
    event, subject, title, body = decision
    assert (event, subject) == ("needs_code", nw["acc"])
    assert title == "icloud2nas：小明 需要验证码 / needs a 2FA code"
    assert body.splitlines() == [
        "Apple 要求重新验证，请打开网页输入验证码。/ "
        "Apple asked for a new 2FA code; open the web page to enter it.",
        "http://nas:8080",
    ]
    nw["settings"].set("public_url", "https://photos.example.com")
    decision = n.decide("account.status", {"account_id": nw["acc"], "status": "needs_reauth"})
    assert decision is not None
    assert decision[3].splitlines()[-1] == "https://photos.example.com"
    # no link at all
    n2 = Notifier(nw["store"], Settings(nw["store"]), nw["bus"], send=nw["rec"])
    nw["settings"].set("public_url", "")
    d2 = n2.decide("space.low", {"min_free_gb": 7})
    assert d2 is not None
    assert len(d2[3].splitlines()) == 1
    assert "7 GB" in d2[3]


def test_repeated_failure_body_has_error_message(nw: dict[str, Any]) -> None:
    with nw["store"].session() as s:
        run = repo.create_run(
            s, account_id=nw["acc"], library_id=nw["lib"], mode="sync", trigger="schedule"
        )
        run.error_message = "iCloud timed out"
        run_id = run.id
    decision = nw["notifier"].decide(
        "run.finished",
        {"run_id": run_id, "status": "failed", "library_id": nw["lib"], "account_failures": 3},
    )
    assert decision is not None
    assert decision[2] == "icloud2nas：小明相册 连续失败 / repeated failures"
    assert decision[3].splitlines()[1] == "iCloud timed out"


async def _settle(nw: dict[str, Any]) -> None:
    await nw["notifier"].drain()


async def test_throttle_24h_per_subject(nw: dict[str, Any]) -> None:
    topic, payload = _six(nw)[0]
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert len(nw["rec"].sent) == 1
    assert nw["rec"].sent[0][0] == ["json://a", "json://b"]
    nw["clock"].now = T0 + timedelta(hours=24) - timedelta(seconds=1)
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert len(nw["rec"].sent) == 1
    nw["clock"].now = T0 + timedelta(hours=24, seconds=1)
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert len(nw["rec"].sent) == 2


async def test_first_full_only_ever_once(nw: dict[str, Any]) -> None:
    topic, payload = _six(nw)[5]
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    nw["clock"].now = T0 + timedelta(days=400)
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert len(nw["rec"].sent) == 1


async def test_failed_send_is_not_recorded(nw: dict[str, Any]) -> None:
    topic, payload = _six(nw)[0]
    nw["rec"].result = False
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    nw["rec"].result = True
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert len(nw["rec"].sent) == 2
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert len(nw["rec"].sent) == 2


async def test_sender_exception_never_propagates(nw: dict[str, Any]) -> None:
    topic, payload = _six(nw)[0]
    nw["rec"].boom = True
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    nw["rec"].boom = False
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert len(nw["rec"].sent) == 1


async def test_no_urls_means_no_send_and_no_record(nw: dict[str, Any]) -> None:
    nw["settings"].set("apprise_urls", "")
    topic, payload = _six(nw)[0]
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert nw["rec"].sent == []
    nw["settings"].set("apprise_urls", "json://a")
    nw["bus"].publish(topic, **payload)
    await _settle(nw)
    assert len(nw["rec"].sent) == 1


async def test_stop_unsubscribes_and_drains(nw: dict[str, Any]) -> None:
    topic, payload = _six(nw)[0]
    nw["bus"].publish(topic, **payload)
    await nw["notifier"].stop()
    assert len(nw["rec"].sent) == 1
    nw["clock"].now = T0 + timedelta(days=2)
    nw["bus"].publish(topic, **payload)
    await asyncio.sleep(0.01)
    assert len(nw["rec"].sent) == 1


# -- session_expiring: housekeeping's tick publishes, the notifier sends once per threshold ------


def _expiring(nw: dict[str, Any], config: Config, expires: datetime, **extra: Any) -> Housekeeping:
    with nw["store"].session() as s:
        acc = repo.get_account(s, nw["acc"])
        assert acc is not None
        acc.status = extra.get("status", "ok")
        acc.session_expires_at = expires
        acc.session_expiry_estimated = extra.get("estimated", False)
    return Housekeeping(config, nw["store"], nw["settings"], nw["bus"], clock=nw["clock"])


async def _tick(nw: dict[str, Any], hk: Housekeeping, at: datetime) -> int:
    nw["clock"].now = at
    hk.check_session_expiry()
    await _settle(nw)
    return len(nw["rec"].sent)


async def test_session_expiring_fires_at_7d_and_1d_once_each(
    nw: dict[str, Any], config: Config
) -> None:
    expires = T0 + timedelta(days=30)
    hk = _expiring(nw, config, expires)
    assert await _tick(nw, hk, expires - timedelta(days=8)) == 0
    assert await _tick(nw, hk, expires - timedelta(days=7)) == 1
    assert await _tick(nw, hk, expires - timedelta(days=6)) == 1
    assert await _tick(nw, hk, expires - timedelta(days=1, seconds=1)) == 1
    assert await _tick(nw, hk, expires - timedelta(days=1)) == 2
    assert await _tick(nw, hk, expires - timedelta(hours=1)) == 2
    assert await _tick(nw, hk, expires + timedelta(hours=1)) == 2  # lapsed: needs_code's job
    (_, first, body), (_, second, _) = nw["rec"].sent
    assert first == (
        "icloud2nas：小明 的 Apple 会话 7 天内到期 / Apple session expires within 7 days"
    )
    assert second == (
        "icloud2nas：小明 的 Apple 会话 1 天内到期 / Apple session expires within a day"
    )
    assert "立即续期" in body and "Renew now" in body
    assert "到期时间 / expires: 2026-11-15 09:00" in body  # T0 + 30 d in Asia/Shanghai
    assert "estimated" not in body


async def test_session_expiring_fires_again_after_a_renewal(
    nw: dict[str, Any], config: Config
) -> None:
    expires = T0 + timedelta(days=30)
    hk = _expiring(nw, config, expires)
    assert await _tick(nw, hk, expires - timedelta(days=3)) == 1
    assert await _tick(nw, hk, expires - timedelta(hours=2)) == 2
    renewed = expires - timedelta(hours=2) + timedelta(days=90)  # code entered just now
    _expiring(nw, config, renewed)
    assert await _tick(nw, hk, expires) == 2  # 90 days left: quiet again
    assert await _tick(nw, hk, renewed - timedelta(days=7)) == 3
    assert await _tick(nw, hk, renewed - timedelta(days=5)) == 3
    assert await _tick(nw, hk, renewed - timedelta(hours=23)) == 4
    assert "7 天内" in nw["rec"].sent[2][1] and "1 天内" in nw["rec"].sent[3][1]


async def test_session_expiring_skips_and_retries(nw: dict[str, Any], config: Config) -> None:
    expires = T0 + timedelta(days=30)
    hk = _expiring(nw, config, expires, status="needs_reauth")
    assert await _tick(nw, hk, expires - timedelta(days=2)) == 0  # a code is already asked for
    hk = _expiring(nw, config, expires, estimated=True)
    nw["settings"].set("notify.session_expiring.enabled", "0")
    assert await _tick(nw, hk, expires - timedelta(days=2)) == 0
    nw["settings"].set("notify.session_expiring.enabled", "1")
    nw["rec"].result = False  # not delivered: not recorded, so the next tick tries again
    assert await _tick(nw, hk, expires - timedelta(days=2)) == 1
    nw["rec"].result = True
    assert await _tick(nw, hk, expires - timedelta(days=2) + timedelta(hours=1)) == 2
    assert await _tick(nw, hk, expires - timedelta(days=2) + timedelta(hours=2)) == 2
    assert "（估计 / estimated）" in nw["rec"].sent[-1][2]


def test_session_expiring_subject_carries_threshold_and_expiry(nw: dict[str, Any]) -> None:
    payload = {"account_id": nw["acc"], "days": 7, "expires_at": "2027-01-19T09:12:13+00:00"}
    decision = nw["notifier"].decide("session.expiring", payload)
    assert decision is not None
    assert decision[:2] == ("session_expiring", f"{nw['acc']}:7d:2027-01-19T09:12:13+00:00")
    assert "2027-01-19T09:12:13+00:00" in decision[3]  # no local time given: the ISO value
    assert nw["notifier"].decide("session.expiring", {**payload, "account_id": "gone"}) is None
