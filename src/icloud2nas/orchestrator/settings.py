"""Typed settings table: every runtime tunable lives here, parsed and validated on write."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from icloud2nas.engine.job import FILE_MATCH_POLICIES, LIVE_PHOTO_POLICIES
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.db import Store

NOTIFY_EVENTS = (
    "needs_code",
    "password_rejected",
    "repeated_failure",
    "plan_pending",
    "space_low",
    "first_full",
)
INTERNAL_KEYS = frozenset({"admin_password_hash", "admin_password_set_at"})


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: str
    parse: Callable[[str], Any]
    validate: Callable[[Any], None]


def _bool(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in ("1", "true"):
        return True
    if lowered in ("0", "false"):
        return False
    raise ValueError(f"not a boolean: {raw!r}")


def _text(raw: str) -> str:
    return raw


def _no_check(value: Any) -> None:
    return None


def _int_range(lo: int, hi: int | None = None) -> Callable[[Any], None]:
    def check(value: Any) -> None:
        if value < lo or (hi is not None and value > hi):
            raise ValueError(f"must be >= {lo}" if hi is None else f"must be {lo}..{hi}")

    return check


def _one_of(choices: tuple[str, ...]) -> Callable[[Any], None]:
    def check(value: Any) -> None:
        if value not in choices:
            raise ValueError(f"must be one of {list(choices)}")

    return check


def _folder_structure(value: Any) -> None:
    if not value or "{" not in value:
        raise ValueError("must be non-empty and contain '{'")


def _public_url(value: Any) -> None:
    if value and not value.startswith(("http://", "https://")):
        raise ValueError("must start with http:// or https://")


def _spec(
    key: str,
    default: str,
    parse: Callable[[str], Any],
    validate: Callable[[Any], None] = _no_check,
) -> SettingSpec:
    return SettingSpec(key, default, parse, validate)


SPECS: dict[str, SettingSpec] = {
    spec.key: spec
    for spec in (
        _spec("max_workers", "2", int, _int_range(1, 8)),
        _spec("min_free_gb", "5", float, _int_range(0)),
        _spec("threads_per_worker", "2", int, _int_range(1, 8)),
        _spec("trash_retention_days", "30", int, _int_range(0)),
        _spec("log_retention_runs", "200", int, _int_range(1)),
        _spec("file_match_policy", "name-id7", _text, _one_of(FILE_MATCH_POLICIES)),
        _spec("live_photo_mov_filename_policy", "original", _text, _one_of(LIVE_PHOTO_POLICIES)),
        _spec("keep_unicode_in_filenames", "1", _bool),
        _spec("folder_structure", "{:%Y/%m}", _text, _folder_structure),
        _spec("paused", "0", _bool),
        _spec("apprise_urls", "", _text),
        _spec("public_url", "", _text, _public_url),
        _spec("admin_password_hash", "", _text),
        _spec("admin_password_set_at", "0", int, _int_range(0)),
        *(_spec(f"notify.{event}.enabled", "1", _bool) for event in NOTIFY_EVENTS),
    )
}

_ENV_SEEDS = {"I2N_MAX_WORKERS": "max_workers", "I2N_MIN_FREE_GB": "min_free_gb"}


class Settings:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _spec_for(key: str) -> SettingSpec:
        spec = SPECS.get(key)
        if spec is None:
            raise ValueError(f"unknown setting {key!r}")
        return spec

    @staticmethod
    def _check(spec: SettingSpec, raw: str) -> None:
        try:
            spec.validate(spec.parse(raw))
        except ValueError as exc:
            raise ValueError(f"{spec.key}: {exc}") from None

    def get_raw(self, key: str) -> str:
        spec = self._spec_for(key)
        with self.store.session() as s:
            return repo.get_setting(s, key, spec.default)

    def get(self, key: str) -> Any:
        spec = self._spec_for(key)
        return spec.parse(self.get_raw(key))

    def set(self, key: str, value: str) -> None:
        self.set_many({key: value})

    def set_many(self, values: Mapping[str, str]) -> None:
        for key, value in values.items():
            self._check(self._spec_for(key), value)
        with self.store.session() as s:
            for key, value in values.items():
                repo.set_setting(s, key, value)

    def all(self) -> dict[str, Any]:
        return {key: self.get(key) for key in SPECS if key not in INTERNAL_KEYS}

    def seed_from_env(self, env: Mapping[str, str]) -> None:
        with self.store.session() as s:
            for env_key, key in _ENV_SEEDS.items():
                raw = env.get(env_key)
                if raw is None or repo.get_setting(s, key, "") != "":
                    continue
                self._check(SPECS[key], raw)
                repo.set_setting(s, key, raw)

    def policy(self) -> dict[str, Any]:
        return {
            "folder_structure": self.get("folder_structure"),
            "file_match_policy": self.get("file_match_policy"),
            "live_photo_mov_filename_policy": self.get("live_photo_mov_filename_policy"),
            "keep_unicode_in_filenames": self.get("keep_unicode_in_filenames"),
            "threads_num": self.get("threads_per_worker"),
        }

    def notify_enabled(self, event: str) -> bool:
        if event not in NOTIFY_EVENTS:
            raise ValueError(f"unknown notification event {event!r}")
        return bool(self.get(f"notify.{event}.enabled"))
