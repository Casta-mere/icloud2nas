"""Worker exit codes and the mapping from pyicloud exceptions to (exit code, error kind)."""

from __future__ import annotations

from enum import IntEnum

from pyicloud_ipd.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudConnectionErrorException,
    PyiCloudFailedLoginException,
    PyiCloudFailedMFAException,
    PyiCloudServiceNotActivatedException,
    PyiCloudServiceUnavailableException,
)


class ExitCode(IntEnum):
    OK = 0
    FAILED = 1
    NEED_MFA = 3
    PASSWORD_REJECTED = 4
    UNAVAILABLE = 5
    FUSED = 6


class HardExit(BaseException):
    """Raised by the test exit hook in place of os._exit so tests can observe the code."""

    def __init__(self, code: ExitCode) -> None:
        super().__init__(f"hard exit {int(code)}")
        self.code = code


def classify(exc: BaseException) -> tuple[ExitCode, str]:
    """Map an exception to the worker exit code and the `kind` field of the error event."""
    if isinstance(exc, PyiCloudFailedLoginException):
        return ExitCode.PASSWORD_REJECTED, "password"
    if isinstance(exc, PyiCloudFailedMFAException):
        return ExitCode.FAILED, "auth"
    if isinstance(exc, PyiCloudServiceUnavailableException):
        return ExitCode.UNAVAILABLE, "unavailable"
    if isinstance(exc, PyiCloudConnectionErrorException):
        return ExitCode.UNAVAILABLE, "network"
    if isinstance(exc, PyiCloudServiceNotActivatedException):
        return ExitCode.FAILED, "not_activated"
    if isinstance(exc, PyiCloudAPIResponseException):
        if exc.code in ("429", "ACCESS_DENIED") or "429" in (exc.reason or ""):
            return ExitCode.UNAVAILABLE, "ratelimit"
        return ExitCode.FAILED, "api"
    return ExitCode.FAILED, "unknown"
