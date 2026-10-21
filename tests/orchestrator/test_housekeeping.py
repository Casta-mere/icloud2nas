from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from icloud2nas.engine.trash import DAY_MARKER
from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.housekeeping import Housekeeping
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.secrets import Secrets

T0 = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)  # 09:00 Shanghai


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class Env:
    def __init__(self, config: Config) -> None:
        engine = make_engine(config.db_path)
        upgrade_db(engine)
        self.store = Store(engine)
        self.bus = EventBus()
        self.settings = Settings(self.store)
        self.clock = Clock(T0)
        self.hk = Housekeeping(
            config, self.store, self.settings, self.bus, clock=self.clock, tick_s=0.01
        )
        secrets = Secrets.from_config(config)
        with self.store.session() as s:
            acc = repo.create_account(
                s,
                display_name="旭刚",
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
                display_name="旭刚",
                target_dir="旭刚",
                interval_min=30,
                nightly_at="03:00",
            )
            self.account_id, self.library_id = acc.id, lib.id

    def make_runs(self, config: Config, n: int, status: str = "ok") -> list[tuple[str, Path]]:
        out: list[tuple[str, Path]] = []
        with self.store.session() as s:
            for i in range(n):
                run = repo.create_run(
                    s,
                    account_id=self.account_id,
                    library_id=self.library_id,
                    mode="sync",
                    trigger="manual",
                )
                run.started_at = T0 + timedelta(minutes=i)
                run.status = status
                log = config.logs_dir / f"{run.id}.log"
                log.write_text("x")
                run.log_path = str(log)
                out.append((run.id, log))
        return out


@pytest.fixture
def hk(config: Config) -> Env:
    return Env(config)


@pytest.fixture
def photos(config: Config) -> Iterator[Path]:
    yield config.photos_dir


def _old_day(photos: Path, day: str) -> Path:
    d = photos / ".trash" / "旭刚" / day
    d.mkdir(parents=True)
    (d / DAY_MARKER).touch()
    (d / "a.jpg").write_bytes(b"abc")
    return d


async def test_prune_keeps_newest_n_and_plan_referenced(hk: Env, config: Config) -> None:
    runs = hk.make_runs(config, 25)
    protected_id, protected_log = runs[0]  # the oldest, but referenced by a plan
    with hk.store.session() as s:
        repo.create_plan(
            s,
            library_id=hk.library_id,
            run_id=protected_id,
            extraneous_count=1,
            plan_file="p.json",
        )
    assert hk.hk.prune_runs() == 4  # 25 - 20 = 5 beyond the limit, minus the protected one
    with hk.store.session() as s:
        left = {r.id for r in repo.list_runs(s, limit=100)}
    assert len(left) == 21 and protected_id in left
    assert protected_log.exists()
    gone = [log for rid, log in runs[1:5]]
    assert not any(p.exists() for p in gone)
    assert all(log.exists() for _, log in runs[5:])


async def test_prune_never_deletes_running(hk: Env, config: Config) -> None:
    hk.settings.set("log_retention_runs", "1")
    running = hk.make_runs(config, 1, status="running")
    hk.make_runs(config, 2)  # newer-or-equal started_at; running one is oldest/equal
    hk.hk.prune_runs()
    with hk.store.session() as s:
        assert repo.get_run(s, running[0][0]) is not None


async def test_purge_once_per_day(hk: Env, photos: Path) -> None:
    calls: list[int] = []
    orig = hk.hk.purge_trash
    hk.hk.purge_trash = lambda: (calls.append(1), orig())[1]  # type: ignore[method-assign]
    stop = asyncio.Event()
    task = asyncio.create_task(hk.hk.run_forever(stop))
    await asyncio.sleep(0.1)
    assert calls == [1]  # many ticks, one purge
    hk.clock.now = T0 + timedelta(days=1)
    await asyncio.sleep(0.1)
    stop.set()
    await task
    assert calls == [1, 1]


async def test_purge_trash_uses_retention(hk: Env, photos: Path) -> None:
    old = _old_day(photos, "2000-01-01")
    hk.clock.now = datetime(2026, 10, 16, tzinfo=UTC)
    assert hk.hk.purge_trash() == [old]
    assert hk.hk.refresh_trash_bytes() == 0 and hk.hk.trash_bytes == 0


async def test_refresh_trash_bytes(hk: Env, photos: Path) -> None:
    _old_day(photos, "2026-10-15")
    assert hk.hk.refresh_trash_bytes() == 3 and hk.hk.trash_bytes == 3


async def test_run_finished_triggers_prune(hk: Env, config: Config) -> None:
    runs = hk.make_runs(config, 22)
    hk.hk.start()
    hk.bus.publish("run.finished", run_id=runs[-1][0], status="ok", library_id=hk.library_id)
    assert not runs[0][1].exists() and not runs[1][1].exists()
    hk.hk.stop()
    more = hk.make_runs(config, 1)
    hk.bus.publish("run.finished", run_id=more[0][0], status="ok", library_id=hk.library_id)
    assert runs[2][1].exists()


async def test_trash_scans_run_off_the_event_loop(hk: Env) -> None:
    def slow_purge() -> list[Path]:
        time.sleep(0.3)
        return []

    def slow_bytes() -> int:
        time.sleep(0.3)
        return 0

    hk.hk.purge_trash = slow_purge  # type: ignore[method-assign]
    hk.hk.refresh_trash_bytes = slow_bytes  # type: ignore[method-assign]
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    task = asyncio.create_task(hk.hk.run_forever(stop))
    lags: list[float] = []
    for _ in range(8):  # spans both slow calls
        began = loop.time()
        await asyncio.sleep(0.05)
        lags.append(loop.time() - began - 0.05)
    stop.set()
    await task
    assert max(lags) < 0.1, lags
