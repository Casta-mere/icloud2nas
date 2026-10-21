"""Settings (including the write-only admin password) and the notification test."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from fastapi import APIRouter, Depends, Request

from icloud2nas.api.auth import hash_password, require_session
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.settings import INTERNAL_KEYS

router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _view(state: AppState) -> dict[str, Any]:
    return {
        "values": state.settings.all(),
        "admin_password_set": bool(state.settings.get("admin_password_hash")),
        "public_url_fallback": state.last_origin,
    }


def _raw(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    return _view(_state(request))


@router.put("/settings")
async def put_settings(body: dict[str, Any], request: Request) -> dict[str, Any]:
    state = _state(request)
    for key, value in body.items():
        if key in INTERNAL_KEYS:
            raise ValueError(f"{key} cannot be set directly")
        if value is None:
            raise ValueError(f"{key}: null is not allowed")
    values = {k: _raw(v) for k, v in body.items() if k != "admin_password"}
    if "admin_password" in body:
        password = body["admin_password"]
        if not isinstance(password, str):
            raise ValueError("admin_password must be a string")
        if password:
            values["admin_password_hash"] = await asyncio.to_thread(hash_password, password)
            values["admin_password_set_at"] = str(int(time.time()))
        else:
            values["admin_password_hash"] = ""
            values["admin_password_set_at"] = "0"
    state.settings.set_many(values)
    if body:
        state.bus.publish("settings.updated", keys=list(body))
    return _view(state)


@router.post("/notify/test")
async def notify_test(request: Request) -> dict[str, bool]:
    state = _state(request)
    urls = [line.strip() for line in str(state.settings.get("apprise_urls")).splitlines()]
    urls = [u for u in urls if u]
    if not urls:
        raise ValueError("apprise_urls is empty")
    body = "通知渠道工作正常。/ Notifications are working."
    link = str(state.settings.get("public_url") or "").strip() or state.last_origin
    if link:
        body += f"\n{link}"
    sent = await asyncio.to_thread(
        state.notifier.send, urls, "icloud2nas 测试通知 / test notification", body
    )
    return {"sent": bool(sent)}
