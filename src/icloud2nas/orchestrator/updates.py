"""Update checker: is a newer release image on ghcr.io? Informs only; never updates anything.

The source of truth is the GHCR tag list (a git tag can exist before its image does). Only plain
`X.Y.Z` tags count, compared numerically. Checks run about a minute after start and then daily,
unless the owner turned them off or the install is still in first-run setup; the Settings button
forces one. The last result is kept in memory and announced with a `version.checked` event.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.metadata
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from icloud2nas.orchestrator.bus import EventBus
from icloud2nas.orchestrator.settings import Settings, access_mode
from icloud2nas.orchestrator.store.models import utcnow

log = logging.getLogger(__name__)

REGISTRY = "https://ghcr.io"
REPOSITORY = "casta-mere/icloud2nas"
COMPARE_BASE = "https://github.com/Casta-mere/icloud2nas/compare"
REQUEST_TIMEOUT_S = 10.0
CHECK_CAP_S = 30.0
MAX_PAGES = 50

_RELEASE = re.compile(r"([0-9]+)\.([0-9]+)\.([0-9]+)")
_ZERO = (0, 0, 0)


def installed_version() -> str:
    try:
        return importlib.metadata.version("icloud2nas")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def parse_release(tag: str) -> tuple[int, int, int] | None:
    """`(X, Y, Z)` for a plain release tag; None for `latest`, `main`, `sha-*`, `v1.2.3`, `1.2`."""
    match = _RELEASE.fullmatch(tag)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def newest_release(tags: Iterable[str]) -> str | None:
    best: tuple[tuple[int, int, int], str] | None = None
    for tag in tags:
        key = parse_release(tag)
        if key is not None and (best is None or key > best[0]):
            best = (key, tag)
    return None if best is None else best[1]


def compare_url(current: str, latest: str) -> str | None:
    """The GitHub compare view between two releases; None when either isn't plain `X.Y.Z`."""
    if parse_release(current) is None or parse_release(latest) is None:
        return None
    return f"{COMPARE_BASE}/v{current}...v{latest}"


@dataclass(frozen=True)
class UpdateResult:
    latest: str | None
    update_available: bool
    checked_at: datetime | None
    error: str | None
    compare_url: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "latest": self.latest,
            "update_available": self.update_available,
            "checked_at": None if self.checked_at is None else self.checked_at.isoformat(),
            "error": self.error,
            "compare_url": self.compare_url,
        }


NO_RESULT = UpdateResult(None, False, None, None, None)


async def fetch_tags(client: httpx.AsyncClient) -> list[str]:
    """Every tag of the image, in registry order (unsorted). `client` has `base_url=REGISTRY`."""
    response = await client.get("/token", params={"scope": f"repository:{REPOSITORY}:pull"})
    response.raise_for_status()
    token = response.json()["token"]
    if not isinstance(token, str):
        raise TypeError("token is not a string")
    headers = {"Authorization": f"Bearer {token}"}
    tags: list[str] = []
    url: str | None = f"/v2/{REPOSITORY}/tags/list?n=1000"
    for _ in range(MAX_PAGES):
        if url is None:
            break
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict):
            raise TypeError("tag list is not an object")
        page = body.get("tags") or []  # an empty repository answers "tags": null
        if not isinstance(page, list):
            raise TypeError("tags is not a list")
        tags.extend(tag for tag in page if isinstance(tag, str))
        url = _next_page(client, response)
    return tags


def _next_page(client: httpx.AsyncClient, response: httpx.Response) -> str | None:
    # ghcr.io sends a path-only link; the bearer token must never follow one anywhere else: not to
    # another host, nor to plain http or another port on the same host
    link = response.links.get("next", {}).get("url")
    if not link:
        return None
    base, target = client.base_url, client.base_url.join(link)
    if (target.scheme, target.host, target.port) != (base.scheme, base.host, base.port):
        return None
    return link


def _failed(previous: UpdateResult, error: str, checked_at: datetime) -> UpdateResult:
    # a failed check keeps what the last good one found (the banner stays) and adds the error
    return UpdateResult(
        previous.latest, previous.update_available, checked_at, error, previous.compare_url
    )


async def _stopped(stop: asyncio.Event, delay_s: float) -> bool:
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), delay_s)
    return stop.is_set()


class UpdateChecker:
    def __init__(
        self,
        settings: Settings,
        bus: EventBus,
        *,
        current: str,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = utcnow,
        startup_delay_s: float = 60.0,
        interval_s: float = 86400.0,
        check_cap_s: float = CHECK_CAP_S,
    ) -> None:
        self.settings = settings
        self.bus = bus
        self.current = current
        self.clock = clock
        self.startup_delay_s = startup_delay_s
        self.interval_s = interval_s
        self.check_cap_s = check_cap_s
        self._transport = transport  # None: httpx's own, honouring HTTPS_PROXY / NO_PROXY
        self._result = NO_RESULT
        self._inflight: asyncio.Task[UpdateResult] | None = None

    @property
    def result(self) -> UpdateResult:
        return self._result

    async def check(self) -> UpdateResult:
        """Check now; a call while a check is running joins it instead of fetching again."""
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._check_once())
        # shielded: one caller giving up (a closed browser tab) doesn't cancel it for the others
        return await asyncio.shield(self._inflight)

    def cancel(self) -> None:
        if self._inflight is not None and not self._inflight.done():
            self._inflight.cancel()

    async def _fetch(self) -> list[str]:
        async with httpx.AsyncClient(
            base_url=REGISTRY, timeout=REQUEST_TIMEOUT_S, transport=self._transport
        ) as client:
            return await fetch_tags(client)

    async def _check_once(self) -> UpdateResult:
        error: str | None = None
        tags: list[str] = []
        try:
            tags = await asyncio.wait_for(self._fetch(), self.check_cap_s)
        except (TimeoutError, httpx.TimeoutException) as exc:
            error = "timed out"
            log.warning("update check timed out: %r", exc)
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            error = f"unexpected response (HTTP {code})"
            log.warning("update check: %s answered HTTP %s", exc.request.url, code)
        except httpx.HTTPError as exc:
            error = "couldn't reach ghcr.io"
            log.warning("update check could not reach %s: %r", REGISTRY, exc)
        except (ValueError, KeyError, TypeError, httpx.InvalidURL) as exc:
            error = "unexpected response"
            log.warning("update check got an unexpected response: %r", exc)
        now = self.clock()
        result = _failed(self._result, error, now) if error else self._found(tags, now)
        self._result = result
        self.bus.publish("version.checked")
        return result

    def _found(self, tags: list[str], now: datetime) -> UpdateResult:
        latest = newest_release(tags)
        key = None if latest is None else parse_release(latest)
        if latest is None or key is None or key <= self._current_key():
            return UpdateResult(latest, False, now, None, None)
        return UpdateResult(latest, True, now, None, compare_url(self.current, latest))

    def _current_key(self) -> tuple[int, int, int]:
        # a dev install (0.0.0) or anything unparseable sees every release as newer
        return parse_release(self.current) or _ZERO

    def _auto_check_due(self) -> bool:
        # read on every tick: the toggle and the access mode change without a restart; nobody
        # has had a chance to turn checks off before first-run setup is done
        return bool(self.settings.get("update_check")) and access_mode(self.settings) != "setup"

    async def run_forever(self, stop: asyncio.Event) -> None:
        delay_s = self.startup_delay_s
        while not await _stopped(stop, delay_s):
            delay_s = self.startup_delay_s
            try:
                if self._auto_check_due():
                    await self.check()
                    delay_s = self.interval_s
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                # cancel() stopped the running check (shutdown): look at `stop` again
            except Exception:  # noqa: BLE001 - the update check must never stop the loop
                log.exception("update check iteration failed")
