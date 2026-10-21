"""Map exceptions raised inside handlers to the API's JSON error shape."""

from __future__ import annotations

from cryptography.fernet import InvalidToken
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from icloud2nas.orchestrator.lock import LockHeld
from icloud2nas.orchestrator.ops import Busy


def _reply(status: int, error: str, exc: Exception) -> JSONResponse:
    return JSONResponse({"error": error, "detail": str(exc)}, status_code=status)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(KeyError)
    async def not_found(_: Request, exc: Exception) -> JSONResponse:
        return _reply(404, "not found", exc)

    @app.exception_handler(ValueError)
    async def invalid(_: Request, exc: Exception) -> JSONResponse:
        return _reply(400, "invalid", exc)

    @app.exception_handler(Busy)
    @app.exception_handler(LockHeld)
    async def busy(_: Request, exc: Exception) -> JSONResponse:
        return _reply(409, "busy", exc)

    @app.exception_handler(IntegrityError)
    async def exists(_: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, IntegrityError)
        return JSONResponse(
            {"error": "already exists", "detail": str(exc.orig)}, status_code=409
        )

    @app.exception_handler(InvalidToken)
    async def cannot_decrypt(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(
            {"error": "cannot decrypt stored passwords — I2N_SECRET_KEY changed?"},
            status_code=500,
        )
