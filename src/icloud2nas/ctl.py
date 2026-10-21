"""icloud2nas-ctl: headless control of the orchestrator core (accounts, libraries, runs, daemon)."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import signal
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TextIO

from cryptography.fernet import InvalidToken
from sqlalchemy.exc import IntegrityError

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config, normalize_hhmm
from icloud2nas.orchestrator.jobs import build_job, validate_job
from icloud2nas.orchestrator.lock import DaemonLock, LockHeld
from icloud2nas.orchestrator.onboarding import Onboarding
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.scheduler import Scheduler
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db
from icloud2nas.orchestrator.store.models import ReconcilePlan
from icloud2nas.orchestrator.store.secrets import Secrets

PASSWORD_ENV = "I2N_CTL_PASSWORD"
PLAN_STATUSES = ("pending", "approved", "executed", "rejected", "superseded")


@dataclass
class App:
    config: Config
    store: Store
    secrets: Secrets
    bus: EventBus
    runs: RunManager
    onboarding: Onboarding
    scheduler: Scheduler
    settings: Settings


def build_app(config: Config, env: Mapping[str, str] | None = None) -> App:
    source = os.environ if env is None else env
    config.ensure_layout()
    engine = make_engine(config.db_path)
    upgrade_db(engine)
    store = Store(engine)
    settings = Settings(store)
    settings.seed_from_env(
        {
            "I2N_MAX_WORKERS": str(config.max_workers),
            "I2N_MIN_FREE_GB": str(config.min_free_gb),
        }
    )
    secrets = Secrets.from_config(config, source)
    bus = EventBus()
    runs = RunManager(config, store, secrets, bus)
    onboarding = Onboarding(config, store, secrets, bus, runs, settings)
    lo, _, hi = source.get("I2N_JITTER_S", "0,300").partition(",")
    scheduler = Scheduler(
        config, store, secrets, bus, runs, onboarding, settings, jitter_s=(int(lo), int(hi or lo))
    )
    return App(config, store, secrets, bus, runs, onboarding, scheduler, settings)


def resolve_id(candidates: Sequence[tuple[str, str]], text: str) -> str:
    """Match a full id, an unambiguous id prefix, or an alias (apple id / name)."""
    if not text:
        raise KeyError(text)
    exact = [i for i, alias in candidates if i == text or alias == text]
    matches = exact or [i for i, _ in candidates if i.startswith(text)]
    if len(matches) > 1:
        raise ValueError(f"ambiguous id {text!r}: {len(matches)} matches")
    if not matches:
        raise KeyError(text)
    return matches[0]


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M")  # stored UTC, shown in TZ
    return str(value)


def _row(out: TextIO, *cols: Any) -> None:
    out.write("\t".join(_fmt(c) for c in cols) + "\n")


def _event_printer(out: TextIO) -> Callable[[dict[str, Any]], None]:
    def show(event: dict[str, Any]) -> None:
        fields = {k: v for k, v in event.items() if k not in ("ev", "ts")}
        out.write(f"[{event.get('ev')}] {json.dumps(fields, ensure_ascii=False)}\n")
        out.flush()

    return show


# -- account ---------------------------------------------------------------------------------------


async def _wait_any(*events: asyncio.Event) -> None:
    waiters = [asyncio.create_task(e.wait()) for e in events]
    try:
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for w in waiters:
            w.cancel()


async def _interactive_auth(
    app: App, account_id: str, ask_code: Callable[[str], str], out: TextIO, trigger: str
) -> int:
    session = await app.onboarding.start_auth(account_id, trigger=trigger)
    printer = _event_printer(out)
    wrong = asyncio.Event()

    def on_bus(topic: str, payload: dict[str, Any]) -> None:
        if topic != "run.event" or payload.get("run_id") != session.run_id:
            return
        event = payload["event"]
        printer(event)
        if event.get("ev") == "mfa_result" and event.get("ok") is False:
            wrong.set()

    unsubscribe = app.bus.subscribe(
        on_bus
    )  # subscribed for the whole session: nothing can be missed
    try:
        await _wait_any(session.prompted, session.finished)
        while not session.finished.is_set():
            wrong.clear()
            code = await asyncio.to_thread(ask_code, "验证码 / 2FA code: ")
            if session.finished.is_set():
                break
            await app.onboarding.submit_code(account_id, code.strip())
            await _wait_any(wrong, session.finished)
    finally:
        unsubscribe()
    await session.finished.wait()
    if session.result is None:
        out.write(f"login failed: {session.error or 'no result'}\n")
        return 1
    return session.result.exit_code


def _password(env: Mapping[str, str], ask_password: Callable[[str], str], apple_id: str) -> str:
    return env.get(PASSWORD_ENV) or ask_password(f"Password for {apple_id}: ")


def _account_ids(app: App) -> list[tuple[str, str]]:
    with app.store.session() as s:
        return [(a.id, a.apple_id) for a in repo.list_accounts(s)]


def cmd_account(
    app: App,
    args: argparse.Namespace,
    env: Mapping[str, str],
    ask_password: Any,
    ask_code: Any,
    out: TextIO,
) -> int:
    if args.action == "add":
        account_id = app.onboarding.create_account(
            display_name=args.name,
            apple_id=args.apple_id,
            region=args.region,
            password=_password(env, ask_password, args.apple_id),
        )
        out.write(f"account {account_id} created; logging in…\n")
        code = asyncio.run(_interactive_auth(app, account_id, ask_code, out, "onboarding"))
        if code != 0:
            out.write(f"login failed with exit code {code}\n")
            return code
        try:
            personal, shared = asyncio.run(app.onboarding.refresh_libraries(account_id))
        except (RuntimeError, ValueError) as exc:
            out.write(
                f"logged in; library discovery failed ({exc}); fix the cause and run: "
                f"icloud2nas-ctl account refresh {account_id}\n"
            )
            return 1
        out.write(f"libraries: personal={personal} shared={shared}\n")
        return 0
    if args.action == "list":
        with app.store.session() as s:
            for a in repo.list_accounts(s):
                _row(
                    out,
                    a.id,
                    a.display_name,
                    a.apple_id,
                    a.region,
                    a.status,
                    a.failures,
                    a.backoff_until,
                    a.last_error[:60] if a.last_error else None,
                )
        return 0
    account_id = resolve_id(_account_ids(app), args.id)
    if args.action == "refresh":
        personal, shared = asyncio.run(app.onboarding.refresh_libraries(account_id))
        out.write(f"libraries: personal={personal} shared={shared}\n")
        return 0
    if args.action == "set-password":
        with app.store.session() as s:
            account = repo.get_account(s, account_id)
            assert account is not None
            apple_id = account.apple_id
        app.onboarding.set_password(account_id, _password(env, ask_password, apple_id))
    return asyncio.run(_interactive_auth(app, account_id, ask_code, out, "reauth"))


# -- library ---------------------------------------------------------------------------------------


def _library_ids(app: App) -> list[tuple[str, str]]:
    with app.store.session() as s:
        return [(lib.id, lib.display_name) for lib in repo.list_libraries(s)]


def cmd_library(app: App, args: argparse.Namespace, out: TextIO) -> int:
    if args.action == "list":
        with app.store.session() as s:
            accounts = {a.id: a.display_name for a in repo.list_accounts(s)}
            for row in repo.list_libraries(s):
                _row(
                    out,
                    row.id,
                    accounts.get(row.account_id),
                    row.display_name,
                    row.zone_name,
                    row.target_dir,
                    row.enabled,
                    row.status,
                    row.interval_min,
                    row.nightly_at,
                    row.last_success_at,
                )
        return 0
    library_id = resolve_id(_library_ids(app), args.id)
    with app.store.session() as s:
        lib = repo.get_library(s, library_id)
        assert lib is not None
        if args.enable:
            lib.enabled = True
        if args.disable:
            lib.enabled = False
        if args.interval is not None:
            lib.interval_min = args.interval
        if args.nightly is not None:
            lib.nightly_at = normalize_hhmm(args.nightly)
        if args.name is not None:
            lib.display_name = args.name
        if args.dir is not None:
            lib.target_dir = repo.validate_target_dir(
                s, args.dir, exclude_library_id=lib.id, kind=lib.kind
            )
    return 0


# -- runs / plans ----------------------------------------------------------------------------------


async def _run_now(app: App, library_id: str, mode: str, out: TextIO) -> int:
    job = app.scheduler.request_manual(library_id, mode)
    printer = _event_printer(out)
    unsubscribe = app.bus.subscribe(
        lambda topic, payload: printer(payload["event"]) if topic == "run.event" else None
    )
    try:
        run_id = await app.scheduler.launch(job)
        await asyncio.gather(*app.scheduler.tasks)
    finally:
        unsubscribe()
    with app.store.session() as s:
        run = repo.get_run(s, run_id)
        return run.exit_code if run is not None and run.exit_code is not None else 1


def cmd_run(app: App, args: argparse.Namespace, out: TextIO) -> int:
    library_id = resolve_id(_library_ids(app), args.id)
    return asyncio.run(_run_now(app, library_id, args.mode, out))


def cmd_runs(app: App, args: argparse.Namespace, out: TextIO) -> int:
    library_id = resolve_id(_library_ids(app), args.library) if args.library else None
    with app.store.session() as s:
        for r in repo.list_runs(s, library_id=library_id, limit=args.limit):
            _row(
                out,
                r.id,
                r.mode,
                r.trigger,
                r.status,
                r.started_at,
                r.ended_at,
                r.exit_code,
                r.error_kind,
                r.error_message,
            )
    return 0


async def _apply_plan(app: App, plan_id: str, out: TextIO) -> int:
    with app.store.session() as s:
        plan = s.get(ReconcilePlan, plan_id)
        if plan is None:
            raise KeyError(plan_id)
        if plan.status != "pending":
            raise ValueError(f"plan is {plan.status}")
        library = repo.get_library(s, plan.library_id)
        assert library is not None
        account = repo.get_account(s, library.account_id)
        assert account is not None
        job = build_job(
            app.config, account, library, interactive=False, policy=app.settings.policy()
        )
        plan_file = plan.plan_file
        validate_job("reconcile", job)
        plan.status = "approved"
    exit_code = 1
    try:
        run_id = app.runs.start_run(
            account_id=account.id, library_id=library.id, mode="reconcile", trigger="manual"
        )
        result = await app.runs.execute(
            run_id,
            mode="reconcile",
            job=job,
            password=None,
            interactive=False,
            apply_plan=plan_file,
            on_event=_event_printer(out),
        )
        exit_code = result.exit_code
    finally:
        with app.store.session() as s:
            plan = s.get(ReconcilePlan, plan_id)
            assert plan is not None
            plan.status = "executed" if exit_code == 0 else "pending"
    return exit_code


def cmd_plan(app: App, args: argparse.Namespace, out: TextIO) -> int:
    if args.action == "list":
        with app.store.session() as s:
            for p in repo.list_plans(s, args.status):
                _row(
                    out, p.id, p.library_id, p.status, p.extraneous_count, p.plan_file, p.created_at
                )
        return 0
    with app.store.session() as s:
        plan_ids = [(p.id, p.id) for p in repo.list_plans(s, None)]
    plan_id = resolve_id(plan_ids, args.id)
    if args.action == "reject":
        with app.store.session() as s:
            plan = s.get(ReconcilePlan, plan_id)
            assert plan is not None
            if plan.status != "pending":
                raise ValueError(f"plan is {plan.status}")
            plan.status = "rejected"
        return 0
    return asyncio.run(_apply_plan(app, plan_id, out))


# -- daemon ----------------------------------------------------------------------------------------


async def _daemon(app: App) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await app.scheduler.run_forever(stop)
    return 0


def cmd_daemon(app: App, out: TextIO) -> int:
    lock = DaemonLock(app.config.lock_path)
    try:
        lock.acquire("daemon")
    except LockHeld as exc:
        out.write(f"error: {exc}\n")
        return 1
    try:  # the startup sweep inside run_forever only happens once we own the lock
        return asyncio.run(_daemon(app))
    finally:
        lock.release()


def _lock_owner(args: argparse.Namespace) -> str | None:
    """The lock owner name for commands that spawn workers; None for DB-only commands."""
    if args.cmd == "run" or (args.cmd == "plan" and args.action == "approve"):
        return f"icloud2nas-ctl {args.cmd} {getattr(args, 'mode', None) or args.action}"
    if args.cmd == "account" and args.action in ("add", "reauth", "set-password", "refresh"):
        return f"icloud2nas-ctl account {args.action}"
    return None


# -- entry -----------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="icloud2nas-ctl")
    sub = p.add_subparsers(dest="cmd", required=True)

    acc = sub.add_parser("account").add_subparsers(dest="action", required=True)
    add = acc.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--apple-id", required=True)
    add.add_argument("--region", choices=["cn", "com"], required=True)
    acc.add_parser("list")
    acc.add_parser("reauth").add_argument("id")
    acc.add_parser("set-password").add_argument("id")
    acc.add_parser("refresh").add_argument("id")

    lib = sub.add_parser("library").add_subparsers(dest="action", required=True)
    lib.add_parser("list")
    st = lib.add_parser("set")
    st.add_argument("id")
    st.add_argument("--enable", action="store_true")
    st.add_argument("--disable", action="store_true")
    st.add_argument("--interval", type=int)
    st.add_argument("--nightly")
    st.add_argument("--dir")
    st.add_argument("--name")

    run = sub.add_parser("run")
    run.add_argument("mode", choices=["sync", "reconcile"])
    run.add_argument("id")

    runs = sub.add_parser("runs")
    runs.add_argument("--library")
    runs.add_argument("--limit", type=int, default=20)

    plan = sub.add_parser("plan").add_subparsers(dest="action", required=True)
    pl = plan.add_parser("list")
    pl.add_argument("--status", default="pending", choices=PLAN_STATUSES)
    plan.add_parser("approve").add_argument("id")
    plan.add_parser("reject").add_argument("id")

    sub.add_parser("daemon")
    return p.parse_args(argv)


def main(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    ask_password: Callable[[str], str] = getpass.getpass,
    ask_code: Callable[[str], str] = input,
    out: TextIO = sys.stdout,
) -> int:
    source = os.environ if env is None else env
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        app = build_app(Config.from_env(source), source)
        if args.cmd == "daemon":
            return cmd_daemon(app, out)
        owner = _lock_owner(args)
        if owner is None:
            return _dispatch(app, args, source, ask_password, ask_code, out)
        with DaemonLock(app.config.lock_path).held(owner):
            return _dispatch(app, args, source, ask_password, ask_code, out)
    except KeyError as exc:
        out.write(f"not found: {exc}\n")
        return 1
    except (ValueError, LockHeld, FileNotFoundError) as exc:
        out.write(f"error: {exc}\n")
        return 1
    except IntegrityError as exc:
        out.write(f"error: already exists ({exc.orig})\n")
        return 1
    except InvalidToken:
        out.write("error: cannot decrypt stored passwords — I2N_SECRET_KEY changed?\n")
        return 1


def _dispatch(
    app: App,
    args: argparse.Namespace,
    env: Mapping[str, str],
    ask_password: Callable[[str], str],
    ask_code: Callable[[str], str],
    out: TextIO,
) -> int:
    if args.cmd == "account":
        return cmd_account(app, args, env, ask_password, ask_code, out)
    if args.cmd == "library":
        return cmd_library(app, args, out)
    if args.cmd == "run":
        return cmd_run(app, args, out)
    if args.cmd == "runs":
        return cmd_runs(app, args, out)
    return cmd_plan(app, args, out)


def cli() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
