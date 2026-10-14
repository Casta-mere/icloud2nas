from pyicloud_ipd.exceptions import (
    PyiCloudAPIResponseException,
    PyiCloudConnectionErrorException,
    PyiCloudFailedLoginException,
    PyiCloudFailedMFAException,
    PyiCloudServiceNotActivatedException,
    PyiCloudServiceUnavailableException,
)

from icloud2nas.engine.errors import ExitCode, HardExit, classify


def test_exit_code_values_are_the_protocol() -> None:
    assert [int(c) for c in ExitCode] == [0, 1, 3, 4, 5, 6]


def test_hard_exit_carries_code() -> None:
    exc = HardExit(ExitCode.NEED_MFA)
    assert exc.code is ExitCode.NEED_MFA
    assert isinstance(exc, BaseException) and not isinstance(exc, Exception)


def test_classify_login_failure_is_password_rejected() -> None:
    assert classify(PyiCloudFailedLoginException("bad")) == (ExitCode.PASSWORD_REJECTED, "password")


def test_classify_mfa_failure() -> None:
    assert classify(PyiCloudFailedMFAException("bad code")) == (ExitCode.FAILED, "auth")


def test_classify_unavailable_and_network() -> None:
    assert classify(PyiCloudServiceUnavailableException("503")) == (
        ExitCode.UNAVAILABLE,
        "unavailable",
    )
    assert classify(PyiCloudConnectionErrorException("down")) == (ExitCode.UNAVAILABLE, "network")


def test_classify_not_activated_before_generic_api() -> None:
    assert classify(PyiCloudServiceNotActivatedException("indexing", None)) == (
        ExitCode.FAILED,
        "not_activated",
    )


def test_classify_rate_limit_by_code_or_reason() -> None:
    assert classify(PyiCloudAPIResponseException("slow down", "429")) == (
        ExitCode.UNAVAILABLE,
        "ratelimit",
    )
    assert classify(PyiCloudAPIResponseException("HTTP 429 Too Many", None)) == (
        ExitCode.UNAVAILABLE,
        "ratelimit",
    )
    assert classify(PyiCloudAPIResponseException("weird", "500")) == (ExitCode.FAILED, "api")


def test_classify_unknown() -> None:
    assert classify(RuntimeError("x")) == (ExitCode.FAILED, "unknown")
