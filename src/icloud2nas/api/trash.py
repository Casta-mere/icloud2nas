"""Trash browsing: list, inspect, rescue ("keep") and permanently delete trash days."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from icloud2nas.api.auth import require_session
from icloud2nas.api.state import AppState
from icloud2nas.engine import trash
from icloud2nas.orchestrator.store import repo

router = APIRouter(prefix="/api/trash", dependencies=[Depends(require_session)])


class KeepBody(BaseModel):
    paths: list[str]


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _rel(value: str) -> str:
    """Library rel: relative, no empty/'.'/'..'/dot-leading components, no trailing slash."""
    rel = value.strip()
    if not rel or rel.startswith("/"):
        raise ValueError(f"invalid library path: {value!r}")
    if any(not part or part.startswith(".") for part in rel.split("/")):
        raise ValueError(f"invalid library path: {value!r}")
    return rel


def _day(value: str) -> date:
    return date.fromisoformat(value)  # ValueError -> 400


def _listing(state: AppState) -> list[dict[str, Any]]:
    photos = state.config.photos_dir
    with state.store.session() as s:
        libraries = [(lib.target_dir, lib.id, lib.display_name) for lib in repo.list_libraries(s)]
    known = {rel for rel, _, _ in libraries}
    entries: list[tuple[str, str | None, str | None]] = list(libraries)
    entries += [(rel, None, None) for rel in trash.list_trash_rels(photos) if rel not in known]
    return [
        {
            "rel": rel,
            "library_id": library_id,
            "display_name": name,
            "days": [
                {"day": d.day.isoformat(), "files": d.files, "bytes": d.bytes}
                for d in trash.list_days(photos, rel)
            ],
        }
        for rel, library_id, name in entries
    ]


@router.get("")
async def list_trash(request: Request) -> list[dict[str, Any]]:
    return await asyncio.to_thread(_listing, _state(request))


@router.get("/{day}")
async def list_day(day: str, rel: str, request: Request) -> list[dict[str, Any]]:
    photos = _state(request).config.photos_dir
    files = await asyncio.to_thread(trash.list_day, photos, _rel(rel), _day(day))
    return [{"rel": f.rel, "size": f.size} for f in files]


@router.delete("/{day}")
async def delete_day(day: str, rel: str, request: Request) -> dict[str, int]:
    photos = _state(request).config.photos_dir
    return {"deleted": await asyncio.to_thread(trash.delete_day, photos, _rel(rel), _day(day))}


@router.post("/{day}/keep")
async def keep(day: str, rel: str, body: KeepBody, request: Request) -> dict[str, list[str]]:
    photos = _state(request).config.photos_dir
    rel = _rel(rel)
    moved = await asyncio.to_thread(trash.keep_files, photos, rel, _day(day), body.paths)
    base = photos / trash.KEPT_DIRNAME / rel
    return {"kept": [p.relative_to(base).as_posix() for p in moved]}
