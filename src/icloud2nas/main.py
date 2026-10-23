"""icloud2nas-serve: the web process (FastAPI + scheduler + housekeeping + notifier)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from icloud2nas.api import (
    accounts,
    auth,
    events,
    hostusers,
    libraries,
    plans,
    runs,
    settings,
    system,
    trash,
)
from icloud2nas.api.errors import install_error_handlers
from icloud2nas.api.events import SseHub
from icloud2nas.api.state import AppState, build_state
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.lock import DaemonLock, LockHeld
from icloud2nas.orchestrator.settings import Settings
from icloud2nas.orchestrator.store.db import Store, make_engine, upgrade_db

SHUTDOWN_CEILING_S = 30
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "host.docker.internal"})


def _hostname(host: str) -> str:
    """The host part of a Host header value ("[::1]:8080" -> "::1", "nas:80" -> "nas")."""
    if host.startswith("["):
        return host[1:].partition("]")[0]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _is_page_load(request: Request) -> bool:
    """A browser navigating to the UI: the only requests whose Host is a link worth sending."""
    if request.headers.get("sec-fetch-mode") == "navigate":
        return True
    return "text/html" in request.headers.get("accept", "")


def _is_json(request: Request) -> bool:
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    return media_type == "application/json"


class CachedStaticFiles(StaticFiles):
    """Hashed build assets never change under their name: cache them for a year."""

    def file_response(
        self, full_path: str | os.PathLike[str], stat_result: os.stat_result, scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the built UI with an SPA fallback; call after every router is included."""
    index = dist / "index.html"
    if not index.is_file():

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        async def not_built() -> PlainTextResponse:
            return PlainTextResponse(
                "icloud2nas API is running; the web UI is not built (see docs/web.md)"
            )

        return
    app.mount("/assets", CachedStaticFiles(directory=dist / "assets"), name="assets")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(path: str) -> Response:
        if path.startswith(("api/", "healthz", "metrics")):
            return JSONResponse({"error": "not found", "detail": "Not Found"}, status_code=404)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(json_lines: bool) -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler(sys.stderr)
    if json_lines:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def create_app(
    config: Config, env: Mapping[str, str] | None = None, *, state: AppState | None = None
) -> FastAPI:
    app_state = state if state is not None else build_state(config, env)
    app_state.sse = SseHub(app_state.bus)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        app_state.notifier.start()
        assert app_state.sse is not None
        app_state.sse.start()
        app_state.housekeeping.start()
        scheduler_task = asyncio.create_task(app_state.scheduler.run_forever(stop))
        housekeeping_task = asyncio.create_task(app_state.housekeeping.run_forever(stop))
        app.state.i2n_scheduler_task = scheduler_task
        try:
            yield
        finally:
            stop.set()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(scheduler_task, housekeeping_task, return_exceptions=True),
                    SHUTDOWN_CEILING_S,
                )
            await app_state.ops.shutdown()
            app_state.sse.stop()
            app_state.housekeeping.stop()
            await app_state.notifier.stop()

    app = FastAPI(title="icloud2nas", lifespan=lifespan)
    app.state.i2n = app_state
    install_error_handlers(app)

    @app.middleware("http")
    async def remember_origin(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        host = request.headers.get("host", request.url.netloc)
        if (
            response.status_code != 401
            and not request.url.path.startswith(("/healthz", "/metrics"))
            and _is_page_load(request)
            and _hostname(host).lower() not in LOOPBACK_HOSTS
        ):
            app_state.last_origin = f"{request.url.scheme}://{host}"
        return response

    @app.middleware("http")
    async def require_json(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # a cross-site form or a no-cors fetch cannot send application/json: CSRF guard
        if (
            request.method in MUTATING
            and request.url.path.startswith("/api/")
            and not _is_json(request)
        ):
            return JSONResponse(
                {"error": "unsupported media type", "detail": "send application/json"},
                status_code=415,
            )
        return await call_next(request)

    app.include_router(system.open_router)
    app.include_router(system.router)
    app.include_router(auth.router)
    for router in (
        accounts.router,
        hostusers.router,
        libraries.router,
        runs.router,
        plans.router,
        events.router,
        trash.router,
        settings.router,
    ):
        app.include_router(router)
    mount_web(app, config.web_dist)
    return app


def reset_admin_password(config: Config) -> None:
    """Clear the admin password; the new set_at invalidates every session cookie issued so far."""
    engine = make_engine(config.db_path)
    try:
        upgrade_db(engine)
        Settings(Store(engine)).set_many(
            {"admin_password_hash": "", "admin_password_set_at": str(int(time.time()))}
        )
    finally:
        engine.dispose()


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="icloud2nas-serve", description="Run the icloud2nas web service."
    )
    parser.add_argument(
        "--reset-admin-password",
        action="store_true",
        help="clear the web UI admin password and exit (the service must be stopped)",
    )
    return parser.parse_args(list(argv))


def serve(argv: Sequence[str]) -> int:
    args = _parse_args(argv)
    configure_logging(True)
    env = os.environ
    config = Config.from_env(env)
    config.ensure_layout()
    lock = DaemonLock(config.lock_path)
    try:
        lock.acquire("icloud2nas-serve")
    except LockHeld as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    try:
        if args.reset_admin_password:
            reset_admin_password(config)
            print("admin password cleared; set a new one in Settings")
            return 0
        import uvicorn

        state = build_state(config, env)
        app = create_app(config, env, state=state)
        uvicorn.run(
            app,
            host=env.get("I2N_HOST") or "0.0.0.0",  # noqa: S104 - container service
            port=int(env.get("I2N_PORT") or 4871),
            log_config=None,
            loop="asyncio",  # uvloop rejects the user=/group= that spawn workers as their owner
            timeout_graceful_shutdown=5,  # an open /api/events tab must not block SIGTERM
        )
        return 0
    finally:
        lock.release()


def cli() -> None:
    sys.exit(serve(sys.argv[1:]))


if __name__ == "__main__":
    cli()
