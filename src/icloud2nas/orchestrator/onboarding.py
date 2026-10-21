"""Account onboarding (spec §5.1): create → interactive auth with code relay → list libraries."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config, normalize_hhmm
from icloud2nas.orchestrator.jobs import (
    MFA_TIMEOUT_S,
    PRIMARY_ZONE,
    build_job,
    validate_job,
)
from icloud2nas.orchestrator.runs import RunManager
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store
from icloud2nas.orchestrator.store.models import Account
from icloud2nas.orchestrator.store.secrets import Secrets
from icloud2nas.orchestrator.supervisor import WorkerProcess, WorkerResult

log = logging.getLogger(__name__)


def safe_dir_name(name: str, fallback: str) -> str:
    cleaned = name.strip().replace("/", "_").replace("\\", "_").lstrip(".").strip()
    return cleaned or fallback


@dataclass
class AuthSession:
    account_id: str
    run_id: str
    process: WorkerProcess
    prompted: asyncio.Event
    finished: asyncio.Event
    result: WorkerResult | None = None
    task: asyncio.Task[None] | None = None
    error: str | None = None


class Onboarding:
    def __init__(
        self,
        config: Config,
        store: Store,
        secrets: Secrets,
        bus: EventBus,
        runs: RunManager,
        settings: Settings,
    ) -> None:
        self.config = config
        self.store = store
        self.secrets = secrets
        self.bus = bus
        self.runs = runs
        self.settings = settings
        self.sessions: dict[str, AuthSession] = {}

    # -- accounts ------------------------------------------------------------------------------

    def create_account(
        self,
        *,
        display_name: str,
        apple_id: str,
        region: str,
        password: str,
        target_dir: str | None = None,
    ) -> str:
        if region not in ("cn", "com"):
            raise ValueError("region must be 'cn' or 'com'")
        with self.store.session() as s:
            account = repo.create_account(
                s,
                display_name=display_name.strip(),
                apple_id=apple_id.strip(),
                region=region,
                password_enc=self.secrets.encrypt(password),
                client_id=str(uuid.uuid4()),
            )
            if target_dir is not None:
                account.primary_dir = repo.validate_target_dir(s, target_dir)
            account_id = account.id
        self.config.cookie_dir(account_id).mkdir(parents=True, exist_ok=True)
        return account_id

    def set_password(self, account_id: str, password: str) -> None:
        with self.store.session() as s:
            account = repo.get_account(s, account_id)
            if account is None:
                raise KeyError(account_id)
            account.password_enc = self.secrets.encrypt(password)
            account.status = "new"
            account.failures = 0
            account.backoff_until = None
            account.last_error = None
        self.bus.publish("account.updated", account_id=account_id)

    def password_for(self, account: Account) -> str:
        return self.secrets.decrypt(account.password_enc)

    def _set_status(self, account_id: str, status: str) -> None:
        with self.store.session() as s:
            account = repo.get_account(s, account_id)
            if account is not None:
                account.status = status
        self.bus.publish("account.status", account_id=account_id, status=status)

    # -- interactive auth ----------------------------------------------------------------------

    async def start_auth(self, account_id: str, *, trigger: str = "onboarding") -> AuthSession:
        if account_id in self.sessions:
            raise ValueError(f"an auth session is already active for {account_id}")
        with self.store.session() as s:
            account = repo.get_account(s, account_id)
            if account is None:
                raise KeyError(account_id)
            job = build_job(
                self.config, account, None, interactive=True, policy=self.settings.policy()
            )
            password = self.password_for(account)
        validate_job("auth", job)
        run_id = self.runs.start_run(
            account_id=account_id, library_id=None, mode="auth", trigger=trigger
        )
        prompted = asyncio.Event()

        def on_event(event: dict[str, Any]) -> None:
            self.bus.publish("run.event", run_id=run_id, event=event)
            if event.get("ev") == "need_mfa":
                self._set_status(account_id, "awaiting_code")
                prompted.set()

        process = WorkerProcess(
            self.config, run_id=run_id, mode="auth", job=job, password=password, interactive=True,
            log_path=self.config.logs_dir / f"{run_id}.log", on_event=on_event,
        )
        try:
            await process.start()
        except Exception as exc:
            self.runs.finish_run(
                run_id,
                WorkerResult(
                    exit_code=1,
                    events=[],
                    terminal={"ev": "error", "kind": "spawn", "message": str(exc)},
                ),
            )
            raise
        session = AuthSession(account_id, run_id, process, prompted, asyncio.Event())
        self.sessions[account_id] = session

        async def finish() -> None:
            try:
                result = await process.wait(timeout_s=MFA_TIMEOUT_S + 60)
                self.runs.finish_run(run_id, result)
                session.result = result
            except Exception as exc:  # noqa: BLE001 - the session must never wedge
                log.exception("auth session for %s failed", account_id)
                session.error = f"{type(exc).__name__}: {exc}"
            finally:
                session.finished.set()
                if self.sessions.get(account_id) is session:
                    self.sessions.pop(account_id, None)

        session.task = asyncio.create_task(finish())
        return session

    async def submit_code(self, account_id: str, code: str) -> None:
        session = self.sessions[account_id]
        if session.finished.is_set():
            raise RuntimeError("auth session already finished")
        await session.process.send_code(code)

    async def wait_auth(self, account_id: str) -> WorkerResult:
        session = self.sessions.get(account_id)
        if session is None:
            raise KeyError(account_id)
        await session.finished.wait()
        if session.result is None:
            raise RuntimeError(session.error or "auth session ended without a result")
        return session.result

    # -- library discovery ---------------------------------------------------------------------

    async def refresh_libraries(self, account_id: str) -> tuple[list[str], list[str]]:
        with self.store.session() as s:
            account = repo.get_account(s, account_id)
            if account is None:
                raise KeyError(account_id)
            job = build_job(
                self.config, account, None, interactive=False, policy=self.settings.policy()
            )
            password = self.password_for(account)
        validate_job("list", job)
        run_id = self.runs.start_run(
            account_id=account_id, library_id=None, mode="list", trigger="onboarding"
        )
        found: dict[str, list[str]] = {}

        def on_event(event: dict[str, Any]) -> None:
            if event.get("ev") == "libraries":
                found["personal"] = [str(z) for z in event.get("personal", [])]
                found["shared"] = [str(z) for z in event.get("shared", [])]

        result = await self.runs.execute(
            run_id, mode="list", job=job, password=password, interactive=False, on_event=on_event
        )
        if result.exit_code != 0 or "personal" not in found:
            terminal = result.terminal or {}
            kind = terminal.get("kind") or (
                "no terminal event" if result.exit_code != 0 else "no libraries event"
            )
            raise RuntimeError(f"list failed (exit {result.exit_code}): {kind}")
        nightly_at = normalize_hhmm(self.config.default_nightly_at)
        with self.store.session() as s:  # personal first, in its own transaction
            account = repo.get_account(s, account_id)
            assert account is not None
            fallback = account.id[:8]
            for zone in found["personal"]:
                if zone != PRIMARY_ZONE:
                    continue  # other private zones are not libraries we mirror
                existing = repo.get_library_by_zone(s, account.id, zone)
                library = repo.upsert_library(
                    s, account, kind="personal", zone_name=zone, display_name=account.display_name,
                    target_dir=account.primary_dir
                    or safe_dir_name(account.display_name, fallback),
                    interval_min=self.config.default_interval_min,
                    nightly_at=nightly_at,
                )
                if existing is None:
                    library.enabled = True  # first discovery only; never override a user's disable
        for zone in found["shared"]:  # each shared zone on its own: one bad zone strands nothing
            with self.store.session() as s:
                other = repo.find_library_by_zone(s, zone)
                if other is not None and other.account_id != account_id:
                    log.info("shared zone %s already recorded by account %s", zone,
                             other.account_id)
                    continue
                account = repo.get_account(s, account_id)
                assert account is not None
                try:
                    repo.upsert_library(
                        s, account, kind="shared", zone_name=zone, display_name=zone,
                        target_dir=f"shared/{safe_dir_name(zone, 'zone')}",
                        interval_min=self.config.default_interval_min,
                        nightly_at=nightly_at,
                    )
                except ValueError as exc:
                    log.warning("shared zone %s not recorded: %s", zone, exc)
        return found["personal"], found["shared"]
