"""Typed repository helpers. The target_dir nesting rule (spec §3.3a) lives here."""

from __future__ import annotations

import posixpath
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from icloud2nas.orchestrator.store.models import (
    Account,
    Library,
    ReconcilePlan,
    Run,
    Setting,
    utcnow,
)

RESERVED_TOP_DIRS = {".trash", "kept"}
SHARED_TOP_DIR = "shared"


def create_account(
    s: Session,
    *,
    display_name: str,
    apple_id: str,
    region: str,
    password_enc: bytes,
    client_id: str,
) -> Account:
    account = Account(
        display_name=display_name,
        apple_id=apple_id,
        region=region,
        password_enc=password_enc,
        client_id=client_id,
    )
    s.add(account)
    s.flush()
    return account


def get_account(s: Session, account_id: str) -> Account | None:
    return s.get(Account, account_id)


def get_account_by_apple_id(s: Session, apple_id: str) -> Account | None:
    return s.scalars(select(Account).where(Account.apple_id == apple_id)).first()


def list_accounts(s: Session) -> list[Account]:
    return list(s.scalars(select(Account).order_by(Account.created_at)))


def _components(target_dir: str) -> tuple[str, ...]:
    return tuple(part for part in target_dir.split("/") if part)


def validate_target_dir(
    s: Session,
    target_dir: str,
    *,
    exclude_library_id: str | None = None,
    kind: str = "personal",
) -> str:
    """Normalise a photos-relative dir and refuse nesting with any other library (spec §3.3a)."""
    if not target_dir or target_dir.startswith("/"):
        raise ValueError("target_dir must be a relative path inside the photos root")
    norm = posixpath.normpath(target_dir)
    parts = _components(norm)
    if not parts or norm == "." or any(p == ".." for p in parts):
        raise ValueError("target_dir must not be empty or escape the photos root")
    if parts[0] in RESERVED_TOP_DIRS or parts[0].startswith("."):
        raise ValueError(f"target_dir must not start with {parts[0]!r} (reserved)")
    if kind == "personal" and parts[0] == SHARED_TOP_DIR:
        raise ValueError(f"target_dir must not start with {SHARED_TOP_DIR!r} (shared libraries)")
    for other in list_libraries(s):
        if other.id == exclude_library_id:
            continue
        theirs = _components(other.target_dir)
        shorter = min(len(parts), len(theirs))
        if parts[:shorter] == theirs[:shorter]:
            msg = (
                f"target_dir {norm!r} nests with library "
                f"{other.display_name!r} ({other.target_dir!r})"
            )
            raise ValueError(msg)
    return norm


def get_library_by_zone(s: Session, account_id: str, zone_name: str) -> Library | None:
    return s.scalars(
        select(Library).where(Library.account_id == account_id, Library.zone_name == zone_name)
    ).first()


def find_library_by_zone(s: Session, zone_name: str) -> Library | None:
    """Any account's library for this zone (shared zones are seen by every participant)."""
    return s.scalars(select(Library).where(Library.zone_name == zone_name)).first()


def upsert_library(
    s: Session,
    account: Account,
    *,
    kind: str,
    zone_name: str,
    display_name: str,
    target_dir: str,
    interval_min: int,
    nightly_at: str,
) -> Library:
    existing = get_library_by_zone(s, account.id, zone_name)
    if existing is not None:
        return existing
    library = Library(
        account_id=account.id,
        kind=kind,
        zone_name=zone_name,
        display_name=display_name,
        target_dir=validate_target_dir(s, target_dir, kind=kind),
        interval_min=interval_min,
        nightly_at=nightly_at,
    )
    s.add(library)
    s.flush()
    return library


def list_libraries(s: Session, *, enabled_only: bool = False) -> list[Library]:
    stmt = select(Library).order_by(Library.display_name)
    if enabled_only:
        stmt = stmt.where(Library.enabled.is_(True))
    return list(s.scalars(stmt))


def get_library(s: Session, library_id: str) -> Library | None:
    return s.get(Library, library_id)


def create_run(
    s: Session, *, account_id: str, library_id: str | None, mode: str, trigger: str
) -> Run:
    run = Run(account_id=account_id, library_id=library_id, mode=mode, trigger=trigger)
    s.add(run)
    s.flush()
    return run


def get_run(s: Session, run_id: str) -> Run | None:
    return s.get(Run, run_id)


def list_runs(s: Session, *, library_id: str | None = None, limit: int = 50) -> list[Run]:
    stmt = select(Run).order_by(Run.started_at.desc()).limit(limit)
    if library_id is not None:
        stmt = stmt.where(Run.library_id == library_id)
    return list(s.scalars(stmt))


def create_plan(
    s: Session, *, library_id: str, run_id: str, extraneous_count: int, plan_file: str
) -> ReconcilePlan:
    plan = ReconcilePlan(
        library_id=library_id, run_id=run_id, extraneous_count=extraneous_count, plan_file=plan_file
    )
    s.add(plan)
    s.flush()
    return plan


def list_plans(s: Session, status: str | None = "pending") -> list[ReconcilePlan]:
    """Plans with this status (None: all)."""
    stmt = select(ReconcilePlan)
    if status is not None:
        stmt = stmt.where(ReconcilePlan.status == status)
    return list(s.scalars(stmt.order_by(ReconcilePlan.created_at)))


def supersede_pending_plans(s: Session, library_id: str) -> int:
    """A newer fused reconcile replaces every older pending plan of the library."""
    pending = list(
        s.scalars(
            select(ReconcilePlan).where(
                ReconcilePlan.library_id == library_id, ReconcilePlan.status == "pending"
            )
        )
    )
    for plan in pending:
        plan.status = "superseded"
    return len(pending)


def get_setting(s: Session, key: str, default: str) -> str:
    row = s.get(Setting, key)
    return row.value if row is not None else default


def set_setting(s: Session, key: str, value: str) -> None:
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value


def sweep_stale_runs(s: Session, message: str) -> int:
    """Mark runs left queued/running by a previous process as failed (daemon restart)."""
    stale = list(s.scalars(select(Run).where(Run.status.in_(("queued", "running")))))
    now: datetime = utcnow()
    for run in stale:
        run.status = "failed"
        run.error_kind = "killed"
        run.error_message = message
        run.ended_at = now
    return len(stale)
