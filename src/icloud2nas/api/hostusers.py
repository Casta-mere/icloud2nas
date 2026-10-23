"""Host users for the per-account file owner select (spec §16.3)."""

from __future__ import annotations

import asyncio
import dataclasses
import posixpath
from typing import Any

from fastapi import APIRouter, Depends, Request

from icloud2nas.api.auth import require_session
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator import owners

router = APIRouter(prefix="/api/host-users", dependencies=[Depends(require_session)])


def _suggested(state: AppState, rel: str) -> dict[str, int] | None:
    norm = posixpath.normpath(rel)
    if rel.startswith("/") or norm == ".." or norm.startswith("../"):
        raise ValueError("path must be relative to the photos root")
    photos = state.config.photos_dir
    try:
        uid, gid = owners.default_owner(photos / norm, photos)
    except (OSError, ValueError):
        return None
    return {"uid": uid, "gid": gid}


@router.get("")
async def host_users(request: Request, path: str | None = None) -> dict[str, Any]:
    """`{privileged, users: [{name, uid, gid, group, home}], suggested: {uid, gid} | null}`."""
    state: AppState = request.app.state.i2n
    users = await asyncio.to_thread(owners.read_host_users, state.config.host_etc)
    return {
        "privileged": owners.privileged(),
        "users": [dataclasses.asdict(u) for u in users],
        "suggested": _suggested(state, path) if path is not None else None,
    }
