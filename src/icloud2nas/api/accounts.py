"""Accounts: onboarding, reauth, 2FA code relay, library discovery."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from icloud2nas.api.auth import require_session
from icloud2nas.api.errors import OwnerFixed
from icloud2nas.api.serializers import account_json, resolve_account
from icloud2nas.api.state import AppState
from icloud2nas.orchestrator import owners
from icloud2nas.orchestrator.onboarding import check_owner
from icloud2nas.orchestrator.ops import Busy
from icloud2nas.orchestrator.store import repo

router = APIRouter(prefix="/api/accounts", dependencies=[Depends(require_session)])


class AccountCreate(BaseModel):
    display_name: str
    apple_id: str
    region: str
    password: str
    target_dir: str | None = None
    owner_uid: int | None = None
    owner_gid: int | None = None


class AccountPatch(BaseModel):
    display_name: str | None = None
    password: str | None = None
    owner_uid: int | None = None
    owner_gid: int | None = None


class CodeBody(BaseModel):
    code: str


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _account(state: AppState, account_id: str) -> dict[str, Any]:
    names = owners.owner_names(state.config.host_etc)
    with state.store.session() as s:
        account = repo.get_account(s, account_id)
        if account is None:
            raise KeyError(account_id)
        ids = [lib.id for lib in repo.libraries_of_account(s, account_id)]
        return account_json(account, ids, names)


@router.get("")
async def list_accounts(request: Request) -> list[dict[str, Any]]:
    state = _state(request)
    names = owners.owner_names(state.config.host_etc)
    with state.store.session() as s:
        return [
            account_json(a, [lib.id for lib in repo.libraries_of_account(s, a.id)], names)
            for a in repo.list_accounts(s)
        ]


@router.post("", status_code=201)
async def create_account(body: AccountCreate, request: Request) -> dict[str, str]:
    state = _state(request)
    account_id = state.onboarding.create_account(
        display_name=body.display_name,
        apple_id=body.apple_id,
        region=body.region,
        password=body.password,
        target_dir=body.target_dir,
        owner_uid=body.owner_uid,
        owner_gid=body.owner_gid,
    )
    state.bus.publish("account.updated", account_id=account_id)
    run_id = await state.ops.start_auth(account_id)
    return {"id": account_id, "run_id": run_id}


@router.get("/{account_id}")
async def get_account(account_id: str, request: Request) -> dict[str, Any]:
    state = _state(request)
    return _account(state, resolve_account(state, account_id))


@router.patch("/{account_id}")
async def patch_account(account_id: str, body: AccountPatch, request: Request) -> dict[str, Any]:
    state = _state(request)
    resolved = resolve_account(state, account_id)
    run_id: str | None = None
    display_name = body.display_name.strip() if body.display_name is not None else None
    if display_name is not None and not display_name:
        raise ValueError("display_name must not be blank")
    owner = check_owner(body.owner_uid, body.owner_gid)
    if owner is not None:  # set once when the account is added (spec §16.3); never changed
        with state.store.session() as s:
            account = repo.get_account(s, resolved)
            if account is None:
                raise KeyError(resolved)
            if (account.owner_uid, account.owner_gid) != owner:
                raise OwnerFixed(
                    "the file owner is set when the account is added and cannot be changed; "
                    "delete the account and add it again to use another owner"
                )
    if body.password is not None:
        state.ops.check_idle(resolved)  # fail before the stored password changes
    if display_name is not None:
        with state.store.session() as s:
            account = repo.get_account(s, resolved)
            if account is None:
                raise KeyError(resolved)
            account.display_name = display_name
        state.bus.publish("account.updated", account_id=resolved)
    if body.password is not None:
        run_id = await state.ops.set_password(resolved, body.password)
    out = _account(state, resolved)
    if run_id is not None:
        out["run_id"] = run_id
    return out


@router.delete("/{account_id}", status_code=204)
async def delete_account(account_id: str, request: Request, keep_dir: bool = True) -> Response:
    state = _state(request)
    state.ops.delete_account(resolve_account(state, account_id), keep_dir=keep_dir)
    return Response(status_code=204)


@router.post("/{account_id}/auth", status_code=202)
async def reauth(account_id: str, request: Request) -> dict[str, str]:
    state = _state(request)
    return {"run_id": await state.ops.start_auth(resolve_account(state, account_id))}


@router.post("/{account_id}/code", status_code=204)
async def submit_code(account_id: str, body: CodeBody, request: Request) -> Response:
    state = _state(request)
    resolved = resolve_account(state, account_id)
    try:
        await state.ops.submit_code(resolved, body.code)
    except (KeyError, RuntimeError):
        raise Busy("no auth session is waiting for a code") from None
    return Response(status_code=204)


@router.post("/{account_id}/refresh", response_model=None)
async def refresh(account_id: str, request: Request) -> Response:
    state = _state(request)
    resolved = resolve_account(state, account_id)
    try:
        personal, shared = await state.ops.refresh_libraries(resolved)
    except Busy:
        raise
    except RuntimeError as exc:
        return JSONResponse({"error": "worker failed", "detail": str(exc)}, status_code=502)
    with state.store.session() as s:
        ids = [lib.id for lib in repo.libraries_of_account(s, resolved)]
    for library_id in ids:
        state.bus.publish("library.updated", library_id=library_id)
    return JSONResponse({"personal": personal, "shared": shared})
