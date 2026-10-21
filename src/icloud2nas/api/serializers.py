"""JSON shapes for the API and id resolution shared by the routers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.models import Account, Library, ReconcilePlan, Run


def resolve(candidates: Sequence[tuple[str, str]], text: str) -> str:
    """Match a full id, an unambiguous id prefix, or an alias (apple id / name)."""
    if not text:
        raise KeyError(text)
    exact = [i for i, alias in candidates if i == text or alias == text]
    matches = list(dict.fromkeys(exact or [i for i, _ in candidates if i.startswith(text)]))
    if len(matches) > 1:
        raise ValueError(f"ambiguous id {text!r}: {len(matches)} matches")
    if not matches:
        raise KeyError(text)
    return matches[0]


def resolve_account(state: AppState, text: str) -> str:
    with state.store.session() as s:
        accounts = repo.list_accounts(s)
        candidates = [(a.id, a.apple_id) for a in accounts]
        candidates += [(a.id, a.display_name) for a in accounts]
    return resolve(candidates, text)


def resolve_library(state: AppState, text: str) -> str:
    with state.store.session() as s:
        return resolve([(lib.id, lib.display_name) for lib in repo.list_libraries(s)], text)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def account_json(a: Account, library_ids: list[str]) -> dict[str, Any]:
    return {
        "id": a.id,
        "display_name": a.display_name,
        "apple_id": a.apple_id,
        "region": a.region,
        "status": a.status,
        "failures": a.failures,
        "backoff_until": _iso(a.backoff_until),
        "last_auth_ok_at": _iso(a.last_auth_ok_at),
        "last_error": a.last_error,
        "created_at": _iso(a.created_at),
        "primary_dir": a.primary_dir,
        "library_ids": library_ids,
    }


def library_json(lib: Library) -> dict[str, Any]:
    return {
        "id": lib.id,
        "account_id": lib.account_id,
        "kind": lib.kind,
        "zone_name": lib.zone_name,
        "display_name": lib.display_name,
        "target_dir": lib.target_dir,
        "enabled": lib.enabled,
        "status": lib.status,
        "interval_min": lib.interval_min,
        "nightly_at": lib.nightly_at,
        "last_run_at": _iso(lib.last_run_at),
        "last_success_at": _iso(lib.last_success_at),
        "first_full_at": _iso(lib.first_full_at),
        "stats": lib.stats,
    }


def run_json(r: Run) -> dict[str, Any]:
    return {
        "id": r.id,
        "account_id": r.account_id,
        "library_id": r.library_id,
        "mode": r.mode,
        "trigger": r.trigger,
        "status": r.status,
        "started_at": _iso(r.started_at),
        "ended_at": _iso(r.ended_at),
        "exit_code": r.exit_code,
        "stats": r.stats,
        "error_kind": r.error_kind,
        "error_message": r.error_message,
        "plan_file": r.plan_file,
    }


def plan_json(
    p: ReconcilePlan, *, paths: list[str] | None = None, total: int | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": p.id,
        "library_id": p.library_id,
        "run_id": p.run_id,
        "created_at": _iso(p.created_at),
        "extraneous_count": p.extraneous_count,
        "plan_file": p.plan_file,
        "status": p.status,
    }
    if paths is not None:
        out["paths"] = paths
    if total is not None:
        out["total"] = total
    return out
