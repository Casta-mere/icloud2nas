"""Runs: history and worker logs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select

from icloud2nas.api.auth import require_session
from icloud2nas.api.serializers import resolve, resolve_account, resolve_library, run_json
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.models import Run

router = APIRouter(prefix="/api/runs", dependencies=[Depends(require_session)])

LOG_TAIL_BYTES = 1024 * 1024


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _resolve_run(state: AppState, text: str) -> str:
    with state.store.session() as s:
        return resolve([(run_id, "") for run_id in s.scalars(select(Run.id))], text)


def _tail(path: Path) -> str:
    with path.open("rb") as fh:
        size = fh.seek(0, 2)
        fh.seek(max(0, size - LOG_TAIL_BYTES))
        return fh.read().decode("utf-8", errors="replace")


@router.get("")
async def list_runs(
    request: Request,
    library_id: str | None = None,
    account_id: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    state = _state(request)
    library = resolve_library(state, library_id) if library_id else None
    account = resolve_account(state, account_id) if account_id else None
    with state.store.session() as s:
        return [
            run_json(r)
            for r in repo.list_runs(s, library_id=library, account_id=account, limit=limit)
        ]


@router.get("/{run_id}")
async def get_run(run_id: str, request: Request) -> dict[str, Any]:
    state = _state(request)
    resolved = _resolve_run(state, run_id)
    with state.store.session() as s:
        run = repo.get_run(s, resolved)
        if run is None:
            raise KeyError(resolved)
        return run_json(run)


@router.get("/{run_id}/log", response_class=PlainTextResponse)
async def get_log(run_id: str, request: Request) -> PlainTextResponse:
    state = _state(request)
    resolved = _resolve_run(state, run_id)
    with state.store.session() as s:
        run = repo.get_run(s, resolved)
        if run is None:
            raise KeyError(resolved)
        log_path = run.log_path
    if log_path is None or not Path(log_path).is_file():
        raise KeyError(f"no log for run {resolved}")
    return PlainTextResponse(_tail(Path(log_path)), media_type="text/plain; charset=utf-8")
