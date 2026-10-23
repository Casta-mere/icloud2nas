"""Trash browsing per library: list, inspect, rescue ("keep") and permanently delete days."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from icloud2nas.api.auth import require_session
from icloud2nas.api.serializers import resolve_library
from icloud2nas.api.state import AppState
from icloud2nas.engine import trash
from icloud2nas.orchestrator import owners
from icloud2nas.orchestrator.ops import Busy
from icloud2nas.orchestrator.store import repo

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/trash", dependencies=[Depends(require_session)])


class KeepBody(BaseModel):
    paths: list[str]


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _library_dir(state: AppState, text: str) -> tuple[str, Path]:
    """(library id, library folder) for an id, id prefix or name; KeyError (404) if unknown."""
    library_id = resolve_library(state, text)
    with state.store.session() as s:
        library = repo.get_library(s, library_id)
        if library is None:
            raise KeyError(text)
        return library_id, state.config.photos_dir / library.target_dir


def _library_owner(state: AppState, library_id: str) -> tuple[int, int] | None:
    with state.store.session() as s:
        library = repo.get_library(s, library_id)
        account = repo.get_account(s, library.account_id) if library is not None else None
        return owners.owner_for(account) if account is not None else None


def _keep_as_owner(
    photos: Path, library_dir: Path, day: date, paths: list[str], owner: tuple[int, int] | None
) -> list[Path]:
    """keep_files; the kept folders it has to make are fchowned to the library's owner."""
    if owner is None:
        return trash.keep_files(library_dir, day, paths, photos=photos)
    held = owner

    def made(fd: int, shown: Path) -> None:
        owners.chown_fd(fd, shown, held)

    return trash.keep_files(library_dir, day, paths, photos=photos, on_create=made)


def _check_not_running(state: AppState, library_id: str) -> None:
    """A running worker may be trashing into this folder right now: refuse to touch it."""
    if library_id in state.scheduler.running_libraries:
        raise Busy("the library is running; try again when it finishes")


def _day(value: str) -> date:
    return date.fromisoformat(value)  # ValueError -> 400


def _days(photos: Path, target_dir: str) -> list[dict[str, Any]]:
    try:
        days = trash.list_days(photos / target_dir, photos=photos)
    except ValueError as exc:  # a symlinked library or store: shown empty, never followed
        log.warning("not listing the trash of %s: %s", target_dir, exc)
        return []
    return [{"day": d.day.isoformat(), "files": d.files, "bytes": d.bytes} for d in days]


def _listing(state: AppState) -> list[dict[str, Any]]:
    photos = state.config.photos_dir
    with state.store.session() as s:
        libraries = [(lib.id, lib.display_name, lib.target_dir) for lib in repo.list_libraries(s)]
    return [
        {
            "library_id": library_id,
            "display_name": name,
            "target_dir": target_dir,
            "days": _days(photos, target_dir),
        }
        for library_id, name, target_dir in libraries
    ]


@router.get("")
async def list_trash(request: Request) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_listing, _state(request))


@router.get("/{day}")
async def list_day(day: str, library: str, request: Request) -> list[dict[str, Any]]:
    _, library_dir = _library_dir(_state(request), library)
    photos = _state(request).config.photos_dir
    files = await asyncio.to_thread(trash.list_day, library_dir, _day(day), photos=photos)
    return [{"rel": f.rel, "size": f.size} for f in files]


@router.delete("/{day}")
async def delete_day(day: str, library: str, request: Request) -> dict[str, int]:
    state = _state(request)
    library_id, library_dir = _library_dir(state, library)
    _check_not_running(state, library_id)
    photos = state.config.photos_dir
    deleted = await asyncio.to_thread(trash.delete_day, library_dir, _day(day), photos=photos)
    return {"deleted": deleted}


@router.post("/{day}/keep")
async def keep(day: str, library: str, body: KeepBody, request: Request) -> dict[str, list[str]]:
    state = _state(request)
    library_id, library_dir = _library_dir(state, library)
    _check_not_running(state, library_id)
    owner = _library_owner(state, library_id)
    moved = await asyncio.to_thread(
        _keep_as_owner, state.config.photos_dir, library_dir, _day(day), body.paths, owner
    )
    base = trash.kept_dir(library_dir)
    return {"kept": [p.relative_to(base).as_posix() for p in moved]}
