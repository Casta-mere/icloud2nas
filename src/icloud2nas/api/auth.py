"""Access mode (setup/lan/wan), the single-admin password and the signed session cookie."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import secrets as _secrets
import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from icloud2nas.api.state import AppState
from icloud2nas.orchestrator.settings import Settings

COOKIE = "i2n_session"
MAX_AGE_S = 30 * 86400
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}
SETUP_REQUIRED = {
    "error": "setup required",
    "detail": "choose LAN or WAN mode in the web UI first",
}
_PROXY_HEADERS = ("forwarded", "x-forwarded-for", "x-real-ip")

AccessMode = Literal["setup", "lan", "wan"]


def hash_password(password: str) -> str:
    salt = _secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(hash_hex)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


def _mac(key: bytes, issued: int) -> str:
    return hmac.new(key, str(issued).encode(), hashlib.sha256).hexdigest()


def sign_session(key: bytes, issued: int) -> str:
    return f"{issued}.{_mac(key, issued)}"


def verify_session(
    key: bytes, token: str, *, not_before: int, max_age_s: int = MAX_AGE_S, now: int
) -> bool:
    issued_text, _, signature = token.partition(".")
    try:
        issued = int(issued_text)
        if not hmac.compare_digest(signature.encode(), _mac(key, issued).encode()):
            return False
    except (ValueError, TypeError, UnicodeError):
        return False
    return issued >= not_before and now - issued <= max_age_s


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _authenticated(state: AppState, request: Request) -> bool:
    token = request.cookies.get(COOKIE)
    if not token:
        return False
    return verify_session(
        state.secrets.derive("i2n-session"),
        token,
        not_before=state.settings.get("admin_password_set_at"),
        now=int(time.time()),
    )


def access_mode(settings: Settings) -> AccessMode:
    # derived, never migrated: a stored password means wan, so an upgrade keeps it locked, and a
    # passwordless install stays in setup until someone chooses lan
    if settings.get("admin_password_hash"):
        return "wan"
    return "lan" if settings.get("access_mode") == "lan" else "setup"


def is_remote(request: Request) -> bool:
    """Whether the page was reached from outside (warning only; nothing is gated on it).

    Reads the raw proxy headers rather than request.client, which uvicorn may rewrite; a forged
    header can only make a request look more remote.
    """
    if any(name in request.headers for name in _PROXY_HEADERS):
        return True
    if request.client is None:
        return False
    try:
        return ipaddress.ip_address(request.client.host).is_global
    except ValueError:
        return False


async def require_session(request: Request) -> None:
    state = _state(request)
    mode = access_mode(state.settings)
    if mode == "setup":
        raise HTTPException(428, SETUP_REQUIRED)
    if mode == "wan" and not _authenticated(state, request):
        raise HTTPException(401, {"error": "unauthorized"})


class LoginBody(BaseModel):
    password: str


router = APIRouter(prefix="/api")


@router.get("/session")
async def get_session(request: Request) -> dict[str, Any]:
    state = _state(request)
    mode = access_mode(state.settings)
    return {
        "mode": mode,
        "auth_required": mode == "wan",
        "authenticated": mode == "wan" and _authenticated(state, request),
        "remote": is_remote(request),
    }


@router.post("/session", status_code=204)
async def login(body: LoginBody, request: Request) -> Response:
    state = _state(request)
    if access_mode(state.settings) != "wan":
        raise ValueError("no admin password is set")
    stored = state.settings.get("admin_password_hash")
    if not await asyncio.to_thread(verify_password, body.password, stored):
        raise HTTPException(401, {"error": "unauthorized"})
    response = Response(status_code=204)
    response.set_cookie(
        COOKIE,
        sign_session(state.secrets.derive("i2n-session"), int(time.time())),
        max_age=MAX_AGE_S,
        httponly=True,
        samesite="lax",
        path="/",
        secure=False,  # TLS is the reverse proxy's job (see docs)
    )
    return response


@router.delete("/session", status_code=204)
async def logout() -> Response:
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax")
    return response


def protected_router() -> APIRouter:
    return APIRouter(prefix="/api", dependencies=[Depends(require_session)])
