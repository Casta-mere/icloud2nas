"""Run lifecycle: create the row, drive the worker, map its exit code onto account/library state."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store
from icloud2nas.orchestrator.store.models import Account, Run, utcnow
from icloud2nas.orchestrator.store.secrets import Secrets
from icloud2nas.orchestrator.supervisor import WorkerResult, run_worker

BACKOFF_BASE_S = 60
BACKOFF_MAX_S = 3600
EXIT_OK, EXIT_FAILED, EXIT_NEED_MFA, EXIT_PASSWORD, EXIT_UNAVAILABLE, EXIT_FUSED = (
    0, 1, 3, 4, 5, 6,
)


class RunManager:
    def __init__(self, config: Config, store: Store, secrets: Secrets, bus: EventBus) -> None:
        self.config = config
        self.store = store
        self.secrets = secrets
        self.bus = bus

    # -- creation ------------------------------------------------------------------------------

    def start_run(self, *, account_id: str, library_id: str | None, mode: str, trigger: str) -> str:
        with self.store.session() as s:
            run = repo.create_run(
                s, account_id=account_id, library_id=library_id, mode=mode, trigger=trigger
            )
            run.status = "running"
            run.log_path = str(self.config.logs_dir / f"{run.id}.log")
            run_id = run.id
        self.bus.publish(
            "run.started", run_id=run_id, account_id=account_id, library_id=library_id, mode=mode
        )
        return run_id

    # -- execution -----------------------------------------------------------------------------

    async def execute(
        self,
        run_id: str,
        *,
        mode: str,
        job: dict[str, Any],
        password: str | None,
        interactive: bool,
        apply_plan: str | None = None,
        timeout_s: float | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> WorkerResult:
        def relay(event: dict[str, Any]) -> None:
            if event.get("ev") == "progress":
                with self.store.session() as s:
                    run = repo.get_run(s, run_id)
                    if run is not None:
                        stats = run.stats
                        stats["progress"] = {
                            k: v for k, v in event.items() if k not in ("ev", "ts")
                        }
                        run.stats_json = json.dumps(stats, ensure_ascii=False)
            self.bus.publish("run.event", run_id=run_id, event=event)
            if on_event is not None:
                on_event(event)

        result = await run_worker(
            self.config,
            run_id=run_id,
            mode=mode,
            job=job,
            password=password,
            interactive=interactive,
            log_path=self.config.logs_dir / f"{run_id}.log",
            apply_plan=apply_plan,
            on_event=relay,
            timeout_s=timeout_s,
        )
        self.finish_run(run_id, result)
        return result

    # -- state machine -------------------------------------------------------------------------

    def finish_run(self, run_id: str, result: WorkerResult) -> Run:
        now = utcnow()
        terminal = result.terminal or {}
        error = terminal if terminal.get("ev") == "error" else {}
        account_status: str | None = None
        plan_pending: tuple[str, int] | None = None
        with self.store.session() as s:
            run = repo.get_run(s, run_id)
            assert run is not None, run_id
            account = repo.get_account(s, run.account_id)
            assert account is not None, run.account_id
            library = repo.get_library(s, run.library_id) if run.library_id else None
            run.ended_at = now
            run.exit_code = result.exit_code
            if library is not None:
                library.last_run_at = now
            code = result.exit_code
            if result.killed:
                self._fail(run, "killed", "worker killed (timeout or shutdown)")
                self._backoff(account)
            elif code == EXIT_OK:
                run.status = "ok"
                stats = run.stats
                stats.update(terminal.get("stats", {}) if terminal.get("ev") == "done" else {})
                run.stats_json = json.dumps(stats, ensure_ascii=False)
                if library is not None:
                    library.last_success_at = now
                account.failures = 0
                account.backoff_until = None
                account.last_auth_ok_at = now
                account.last_error = None
                if account.status != "ok":
                    account.status = "ok"
                    account_status = "ok"
            elif code == EXIT_NEED_MFA:
                run.status = "needs_mfa"
                account.status = "needs_reauth"
                account_status = "needs_reauth"
            elif code == EXIT_PASSWORD:
                self._fail(
                    run, error.get("kind", "password"), error.get("message", "password rejected")
                )
                account.status = "password_rejected"
                account.last_error = run.error_message
                account_status = "password_rejected"
            elif code == EXIT_FUSED and terminal.get("ev") == "reconcile_plan":
                if int(terminal.get("remote_total", 0)) == 0:
                    self._fail(
                        run, "empty_listing", "remote listing returned zero assets; retry later"
                    )
                    self._backoff(account)
                else:
                    run.status = "fused"
                    run.plan_file = str(terminal.get("plan_file"))
                    plan = repo.create_plan(
                        s,
                        library_id=run.library_id or "",
                        run_id=run.id,
                        extraneous_count=int(terminal.get("extraneous", 0)),
                        plan_file=run.plan_file,
                    )
                    plan_pending = (plan.id, plan.extraneous_count)
            else:
                kind = error.get("kind") or ("no terminal event" if not error else "unknown")
                message = error.get("message") or f"worker exited with code {code}"
                self._fail(run, kind, message)
                account.last_error = message
                self._backoff(account)
            status = run.status
            library_id = run.library_id
        self.bus.publish("run.finished", run_id=run_id, status=status, library_id=library_id)
        if account_status is not None:
            self.bus.publish("account.status", account_id=account.id, status=account_status)
        if plan_pending is not None:
            self.bus.publish(
                "plan.pending",
                plan_id=plan_pending[0],
                extraneous=plan_pending[1],
                library_id=library_id,
            )
        return run

    @staticmethod
    def _fail(run: Run, kind: str, message: str) -> None:
        run.status = "failed"
        run.error_kind = kind
        run.error_message = message

    @staticmethod
    def _backoff(account: Account) -> None:
        account.failures += 1
        delay = min(BACKOFF_MAX_S, BACKOFF_BASE_S * 2 ** (account.failures - 1))
        account.backoff_until = datetime.now(UTC) + timedelta(seconds=delay)
