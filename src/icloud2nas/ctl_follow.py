"""Talking to icloud2nas-serve: the HTTP client, the SSE reader and the run/auth followers."""

from __future__ import annotations

import contextlib
import json
import queue
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any, TextIO

import httpx

ADMIN_PASSWORD_ENV = "I2N_ADMIN_PASSWORD"  # the web UI / API admin password
HELLO_TIMEOUT_S = 10.0
AUTH_TIMEOUT_S = 15 * 60  # MFA_TIMEOUT_S (10 min) + 5 min
DISCOVERY_TIMEOUT_S = 120.0
RUN_TIMEOUT_S = 13 * 3600  # worker RUN_TIMEOUT_S (12 h) + 1 h


class CtlError(Exception):
    """A failure to report as `error: <message>` with exit code 1."""


def event_printer(out: TextIO) -> Callable[[dict[str, Any]], None]:
    def show(event: dict[str, Any]) -> None:
        fields = {k: v for k, v in event.items() if k not in ("ev", "ts")}
        out.write(f"[{event.get('ev')}] {json.dumps(fields, ensure_ascii=False)}\n")
        out.flush()

    return show


# -- http ------------------------------------------------------------------------------------------


def _error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip() or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        inner = body.get("detail", body)  # FastAPI wraps HTTPException bodies in {"detail": ...}
        if isinstance(inner, dict):
            return str(inner.get("detail") or inner.get("error") or inner)
        if body.get("detail") and body.get("error"):
            return str(body["detail"])
        return str(inner or body.get("error") or f"HTTP {response.status_code}")
    return str(body)


class Api:
    def __init__(
        self,
        client: httpx.Client,
        base_url: str,
        env: Mapping[str, str],
        ask_password: Callable[[str], str],
    ) -> None:
        self.client = client
        self.base = base_url.rstrip("/")
        self._env = env
        self._ask_password = ask_password
        self._logged_in = False

    def url(self, path: str) -> str:
        return self.base + path

    def login(self) -> None:
        password = self._env.get(ADMIN_PASSWORD_ENV) or self._ask_password("Admin password: ")
        try:
            response = self.client.post(self.url("/api/session"), json={"password": password})
        except httpx.HTTPError as exc:
            raise CtlError(f"cannot reach {self.base}: {exc}") from exc
        if response.status_code >= 400:
            raise CtlError("login failed: wrong admin password")
        self._logged_in = True

    def ensure_session(self) -> None:
        """Log in up front when the server wants it (the event stream cannot retry a 401)."""
        try:
            response = self.client.get(self.url("/api/session"))
        except httpx.HTTPError as exc:
            raise CtlError(f"cannot reach {self.base}: {exc}") from exc
        if response.status_code == 200:
            body = response.json()
            if body.get("auth_required") and not body.get("authenticated"):
                self.login()

    def call(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        allow: Sequence[int] = (),
    ) -> httpx.Response:
        try:
            response = self._send(method, path, json, params)
            if response.status_code == 401 and not self._logged_in:
                self.login()
                response = self._send(method, path, json, params)
        except httpx.HTTPError as exc:
            raise CtlError(f"cannot reach {self.base}: {exc}") from exc
        if response.status_code >= 400 and response.status_code not in allow:
            text = _error_text(response)
            raise CtlError(f"not found: {text}" if response.status_code == 404 else text)
        return response

    def _send(
        self, method: str, path: str, body: Any, params: Mapping[str, Any] | None
    ) -> httpx.Response:
        if body is None and method.upper() != "GET":
            body = {}  # the server wants application/json on every mutating request
        return self.client.request(method, self.url(path), json=body, params=params)

    def get(self, path: str, **params: Any) -> Any:
        return self.call("GET", path, params=params or None).json()

    def send(self, method: str, path: str, body: Any = None) -> Any:
        response = self.call(method, path, json=body)
        return response.json() if response.content else None


# -- server-sent events ----------------------------------------------------------------------------


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield (topic, payload) per `event:`/`data:` frame; comments and bad frames are skipped."""
    topic = "message"
    data: list[str] = []
    for raw in lines:
        line = raw.rstrip("\r\n")
        if not line:
            if data:
                try:
                    payload = json.loads("\n".join(data))
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    yield topic, payload
            topic, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            topic = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))


class EventStream:
    """`GET /api/events` read by a daemon thread into a queue; ready once `hello` arrived."""

    def __init__(self, api: Api) -> None:
        self._api = api
        self._queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._stop = threading.Event()
        self._response: httpx.Response | None = None
        self._thread = threading.Thread(target=self._pump, name="i2n-ctl-events", daemon=True)

    def __enter__(self) -> EventStream:
        self._api.ensure_session()
        self._thread.start()
        try:
            self.wait_for(time.monotonic() + HELLO_TIMEOUT_S, lambda t, _: t == "hello")
        except TimeoutError:
            self.close()
            raise CtlError("event stream did not start") from None
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._stop.set()
        if self._response is not None:
            with contextlib.suppress(Exception):  # best effort; the thread is a daemon
                self._response.close()
        self._thread.join(timeout=2)

    def _pump(self) -> None:
        failure = "event stream closed by the server"
        try:
            with self._api.client.stream("GET", self._api.url("/api/events")) as response:
                self._response = response
                if response.status_code >= 400:
                    response.read()
                    failure = _error_text(response)
                else:
                    for item in parse_sse(response.iter_lines()):
                        self._queue.put(item)
        except Exception as exc:  # noqa: BLE001 - reported to the consumer below
            failure = f"event stream failed: {exc}"
        if not self._stop.is_set():
            self._queue.put(("__error__", {"detail": failure}))

    def get(self, deadline: float) -> tuple[str, dict[str, Any]]:
        """Next event; TimeoutError past the monotonic deadline, CtlError if the stream broke."""
        remaining = deadline - time.monotonic()
        try:
            topic, data = self._queue.get(timeout=max(remaining, 0.0))
        except queue.Empty:
            raise TimeoutError from None
        if topic == "__error__":
            raise CtlError(str(data["detail"]))
        return topic, data

    def wait_for(
        self, deadline: float, match: Callable[[str, dict[str, Any]], bool]
    ) -> tuple[str, dict[str, Any]]:
        while True:
            topic, data = self.get(deadline)
            if match(topic, data):
                return topic, data


def _submit_code(api: Api, account_id: str, code: str) -> None:
    # 409: the session ended meanwhile; its run.finished is already on the way
    api.call("POST", f"/api/accounts/{account_id}/code", json={"code": code.strip()}, allow=(409,))


def follow_auth(
    api: Api,
    start: Callable[[], str],
    ask_code: Callable[[str], str],
    out: TextIO,
) -> int:
    """Run an interactive login: print its events, answer 2FA prompts, return the exit code.

    `start()` performs the POST and returns the auth run id; the stream is open before it runs,
    so no event can be missed, and the account is learned from that run's `run.started`.
    After a successful login it waits for the automatic library discovery (the account stays
    busy until then) and prints the libraries.
    """
    printer = event_printer(out)
    prompt = "验证码 / 2FA code: "
    with EventStream(api) as events:
        run_id = start()
        deadline = time.monotonic() + AUTH_TIMEOUT_S
        exit_code: int | None = None
        account_id = ""
        try:
            while exit_code is None:
                topic, data = events.get(deadline)
                if topic == "run.started" and data.get("run_id") == run_id:
                    account_id = data["account_id"]
                elif topic == "account.status":
                    if data.get("account_id") == account_id and data["status"] == "awaiting_code":
                        _submit_code(api, account_id, ask_code(prompt))
                elif topic == "run.event" and data.get("run_id") == run_id:
                    printer(data["event"])
                    event = data["event"]
                    if event.get("ev") == "mfa_result" and event.get("ok") is False:
                        _submit_code(api, account_id, ask_code(prompt))
                elif topic == "run.finished" and data.get("run_id") == run_id:
                    exit_code = data["exit_code"] if data["exit_code"] is not None else 1
            if exit_code != 0:
                out.write(f"login failed with exit code {exit_code}\n")
                return int(exit_code)
            return _await_discovery(api, events, account_id, out)
        except TimeoutError:
            out.write("error: timed out waiting for the login to finish\n")
            return 1


def _await_discovery(api: Api, events: EventStream, account_id: str, out: TextIO) -> int:
    hint = (
        "logged in; library discovery failed ({}); fix the cause and run: "
        f"icloud2nas-ctl account refresh {account_id}\n"
    )
    try:
        _, done = events.wait_for(
            time.monotonic() + DISCOVERY_TIMEOUT_S,
            lambda t, d: t == "run.finished"
            and d.get("mode") == "list"
            and d.get("account_id") == account_id,
        )
    except TimeoutError:
        out.write(hint.format("no answer"))
        return 1
    if done["exit_code"] != 0:
        out.write(hint.format(f"exit code {done['exit_code']}"))
        return 1
    libraries = api.get("/api/libraries")
    print_libraries(
        out,
        [lib for lib in libraries if lib["account_id"] == account_id and lib["kind"] == "personal"],
        [lib for lib in libraries if lib["account_id"] == account_id and lib["kind"] != "personal"],
    )
    return 0


def print_libraries(out: TextIO, personal: list[Any], shared: list[Any]) -> None:
    def names(items: list[Any]) -> list[str]:
        return [str(i["zone_name"]) if isinstance(i, dict) else str(i) for i in items]

    out.write(f"libraries: personal={names(personal)} shared={names(shared)}\n")


def follow_run(
    api: Api,
    library_id: str,
    start: Callable[[], object],
    out: TextIO,
    *,
    until: Callable[[str, dict[str, Any]], int | None] | None = None,
) -> int:
    """Start a manual request and follow its run until `run.finished` (or `until` answers).

    Our run is the first `run.started` for the library with trigger `manual` after the POST.
    Returns the run's exit code (1 when it has none).
    """
    printer = event_printer(out)
    with EventStream(api) as events:
        start()
        deadline = time.monotonic() + RUN_TIMEOUT_S
        run_id: str | None = None
        try:
            while True:
                topic, data = events.get(deadline)
                if until is not None and (answer := until(topic, data)) is not None:
                    return answer
                if topic == "run.started" and run_id is None:
                    if data.get("library_id") == library_id and data.get("trigger") == "manual":
                        run_id = data["run_id"]
                elif topic == "run.event" and run_id is not None and data["run_id"] == run_id:
                    printer(data["event"])
                elif (
                    topic == "run.finished"
                    and until is None
                    and data["run_id"] == run_id
                ):
                    return int(data["exit_code"]) if data["exit_code"] is not None else 1
        except TimeoutError:
            out.write("error: timed out waiting for the run to finish\n")
            return 1
