"""Libraries: settings, manual runs, pause/resume."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from icloud2nas.api.auth import require_session
from icloud2nas.api.serializers import library_json, resolve_library
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.ops import Busy
from icloud2nas.orchestrator.store import repo

router = APIRouter(prefix="/api/libraries", dependencies=[Depends(require_session)])


class LibraryPatch(BaseModel):
    display_name: str | None = None
    target_dir: str | None = None
    enabled: bool | None = None
    interval_min: int | None = None
    nightly_at: str | None = None


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _library(state: AppState, library_id: str) -> dict[str, Any]:
    with state.store.session() as s:
        library = repo.get_library(s, library_id)
        if library is None:
            raise KeyError(library_id)
        return library_json(library)


@router.get("")
async def list_libraries(request: Request) -> list[dict[str, Any]]:
    state = _state(request)
    with state.store.session() as s:
        return [library_json(lib) for lib in repo.list_libraries(s)]


@router.get("/{library_id}")
async def get_library(library_id: str, request: Request) -> dict[str, Any]:
    state = _state(request)
    return _library(state, resolve_library(state, library_id))


@router.patch("/{library_id}")
async def patch_library(library_id: str, body: LibraryPatch, request: Request) -> dict[str, Any]:
    state = _state(request)
    resolved = resolve_library(state, library_id)
    state.ops.set_library(
        resolved,
        display_name=body.display_name,
        target_dir=body.target_dir,
        enabled=body.enabled,
        interval_min=body.interval_min,
        nightly_at=body.nightly_at,
    )
    return _library(state, resolved)


def _queue(request: Request, library_id: str, mode: str) -> JSONResponse:
    state = _state(request)
    resolved = resolve_library(state, library_id)
    try:
        state.ops.request_run(resolved, "sync" if mode == "sync" else "reconcile")
    except Busy:
        raise  # the Busy handler answers 409 with its own detail (e.g. folder missing)
    except RuntimeError:
        return JSONResponse({"error": "busy", "detail": "space low"}, status_code=409)
    return JSONResponse({"queued": True}, status_code=202)


@router.post("/{library_id}/sync", response_model=None)
async def sync(library_id: str, request: Request) -> JSONResponse:
    return _queue(request, library_id, "sync")


@router.post("/{library_id}/reconcile", response_model=None)
async def reconcile(library_id: str, request: Request) -> JSONResponse:
    return _queue(request, library_id, "reconcile")


@router.post("/{library_id}/pause")
async def pause(library_id: str, request: Request) -> dict[str, Any]:
    state = _state(request)
    resolved = resolve_library(state, library_id)
    state.ops.pause_library(resolved)
    return _library(state, resolved)


@router.post("/{library_id}/resume")
async def resume(library_id: str, request: Request) -> dict[str, Any]:
    state = _state(request)
    resolved = resolve_library(state, library_id)
    state.ops.resume_library(resolved)
    return _library(state, resolved)
