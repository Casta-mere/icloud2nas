"""Reconcile plans: review, approve, reject."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from icloud2nas.api.auth import require_session
from icloud2nas.api.serializers import plan_json, resolve
from icloud2nas.api.state import AppState
from icloud2nas.engine.reconcile import read_plan
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.models import ReconcilePlan

router = APIRouter(prefix="/api/plans", dependencies=[Depends(require_session)])

PLAN_STATUSES = ("pending", "approved", "executed", "rejected", "superseded")
PATH_PREVIEW = 1000


def _state(request: Request) -> AppState:
    state: AppState = request.app.state.i2n
    return state


def _resolve_plan(state: AppState, text: str) -> str:
    with state.store.session() as s:
        return resolve([(p.id, "") for p in repo.list_plans(s, None)], text)


def _plan(state: AppState, plan_id: str) -> dict[str, Any]:
    with state.store.session() as s:
        plan = s.get(ReconcilePlan, plan_id)
        if plan is None:
            raise KeyError(plan_id)
        return plan_json(plan)


@router.get("")
async def list_plans(request: Request, status: str = "pending") -> list[dict[str, Any]]:
    if status != "all" and status not in PLAN_STATUSES:
        raise ValueError(f"status must be one of {', '.join(PLAN_STATUSES)} or all")
    state = _state(request)
    with state.store.session() as s:
        return [
            plan_json(p) for p in repo.list_plans(s, None if status == "all" else status)
        ]


@router.get("/{plan_id}")
async def get_plan(plan_id: str, request: Request) -> dict[str, Any]:
    state = _state(request)
    resolved = _resolve_plan(state, plan_id)
    with state.store.session() as s:
        plan = s.get(ReconcilePlan, resolved)
        if plan is None:
            raise KeyError(resolved)
        try:
            lines = read_plan(Path(plan.plan_file))
        except FileNotFoundError:
            lines = []
        return plan_json(plan, paths=lines[:PATH_PREVIEW], total=len(lines))


@router.post("/{plan_id}/approve", response_model=None)
async def approve(plan_id: str, request: Request) -> JSONResponse:
    state = _state(request)
    resolved = _resolve_plan(state, plan_id)
    state.ops.approve_plan(resolved)
    return JSONResponse(_plan(state, resolved), status_code=202)


@router.post("/{plan_id}/reject")
async def reject(plan_id: str, request: Request) -> dict[str, Any]:
    state = _state(request)
    resolved = _resolve_plan(state, plan_id)
    state.ops.reject_plan(resolved)
    return _plan(state, resolved)
