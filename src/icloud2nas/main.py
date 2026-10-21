"""icloud2nas-serve: the web process (FastAPI + scheduler + housekeeping + notifier)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, Response

from icloud2nas.api import accounts, auth, events, libraries, plans, runs, system
from icloud2nas.api.errors import install_error_handlers
from icloud2nas.api.events import SseHub
from icloud2nas.api.state import AppState, build_state
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.lock import DaemonLock, LockHeld

SHUTDOWN_CEILING_S = 30


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
        path = request.url.path
        if not path.startswith(("/healthz", "/metrics")):
            host = request.headers.get("host", request.url.netloc)
            app_state.last_origin = f"{request.url.scheme}://{host}"
        return await call_next(request)

    app.include_router(system.open_router)
    app.include_router(system.router)
    app.include_router(auth.router)
    for router in (accounts.router, libraries.router, runs.router, plans.router, events.router):
        app.include_router(router)
    return app


def serve(argv: Sequence[str]) -> int:
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
        import uvicorn

        state = build_state(config, env)
        app = create_app(config, env, state=state)
        uvicorn.run(
            app,
            host=env.get("I2N_HOST") or "0.0.0.0",  # noqa: S104 - container service
            port=int(env.get("I2N_PORT") or 8080),
            log_config=None,
        )
        return 0
    finally:
        lock.release()


def cli() -> None:
    sys.exit(serve(sys.argv[1:]))


if __name__ == "__main__":
    cli()
