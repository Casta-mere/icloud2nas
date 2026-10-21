"""Health, metrics and global status/pause."""

from __future__ import annotations

import importlib.metadata
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from icloud2nas.api.auth import require_session
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.store import repo

open_router = APIRouter()
router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _version() -> str:
    try:
        return importlib.metadata.version("icloud2nas")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


@open_router.get("/healthz", response_model=None)
async def healthz(request: Request) -> JSONResponse:
    state = _state(request)
    try:
        with state.store.session() as s:
            s.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any DB failure means unhealthy
        return JSONResponse({"ok": False, "detail": f"database: {exc}"}, status_code=503)
    task = getattr(request.app.state, "i2n_scheduler_task", None)
    if task is None or task.done():
        return JSONResponse({"ok": False, "detail": "scheduler is not running"}, status_code=503)
    return JSONResponse({"ok": True})


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(**labels: str) -> str:
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in labels.items()) + "}"


def render_metrics(state: AppState) -> str:
    out: list[str] = []

    def metric(name: str, help_text: str, samples: list[tuple[str, float | int]]) -> None:
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} gauge")
        out.extend(f"{name}{labels} {value}" for labels, value in samples)

    files: list[tuple[str, float | int]] = []
    last_success: list[tuple[str, float | int]] = []
    statuses: list[tuple[str, float | int]] = []
    with state.store.session() as s:
        for lib in repo.list_libraries(s):
            label = _labels(library_id=lib.id, library=lib.display_name)  # names can repeat
            count = lib.stats.get("files")
            if count is not None:
                files.append((label, count))
            if lib.last_success_at is not None:
                last_success.append((label, lib.last_success_at.timestamp()))
        for account in repo.list_accounts(s):
            statuses.append((_labels(account=account.display_name, status=account.status), 1))
    metric("i2n_library_files", "Files mirrored per library at the last run.", files)
    metric(
        "i2n_library_last_success_timestamp",
        "Unix time of the last successful run per library.",
        last_success,
    )
    metric("i2n_account_status", "Current account status (value is always 1).", statuses)
    metric("i2n_trash_bytes", "Bytes held in the trash.", [("", state.housekeeping.trash_bytes)])
    metric(
        "i2n_workers_running", "Worker processes running.", [("", len(state.scheduler.active))]
    )
    metric("i2n_up", "Always 1 while the process serves requests.", [("", 1)])
    return "\n".join(out) + "\n"


@open_router.get("/metrics", response_class=PlainTextResponse)
async def metrics(request: Request) -> PlainTextResponse:
    return PlainTextResponse(
        render_metrics(_state(request)), media_type="text/plain; version=0.0.4; charset=utf-8"
    )


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    state = _state(request)
    return {
        "paused": state.settings.get("paused"),
        "space_low": state.scheduler.space_low,
        "workers_running": len(state.scheduler.active),
        "version": _version(),
        "started_at": state.started_at.isoformat(),
    }


@router.post("/pause")
async def pause(request: Request) -> dict[str, bool]:
    _state(request).ops.pause_all()
    return {"paused": True}


@router.post("/resume")
async def resume(request: Request) -> dict[str, bool]:
    _state(request).ops.resume_all()
    return {"paused": False}
