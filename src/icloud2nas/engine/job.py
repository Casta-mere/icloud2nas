"""Job specification for one worker run.

Mode/run-id from argv, the rest from the I2N_JOB env var.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from typing import Any

MODES: tuple[str, ...] = ("auth", "list", "sync", "reconcile")
REGIONS: tuple[str, ...] = ("cn", "com")
FILE_MATCH_POLICIES: tuple[str, ...] = ("name-id7", "name-size-dedup-with-suffix")
LIVE_PHOTO_POLICIES: tuple[str, ...] = ("original", "suffix")
PRIMARY_ZONE = "PrimarySync"

JOB_ENV = "I2N_JOB"
PASSWORD_ENV = "I2N_PASSWORD"

MAX_RETRIES = 10
MAX_RETRY_WAIT_S = 3600


def _retry_waits(raw: Any) -> tuple[int, ...]:
    """`retry_waits_s` from JSON: a list of whole seconds, one wait per retry; [] disables."""
    if not isinstance(raw, list):
        raise ValueError("retry_waits_s must be a list of seconds")
    if len(raw) > MAX_RETRIES:
        raise ValueError(f"retry_waits_s allows at most {MAX_RETRIES} retries")
    for wait in raw:
        if isinstance(wait, bool) or not isinstance(wait, int):
            raise ValueError(f"retry_waits_s entries must be whole seconds, got {wait!r}")
        if not 0 <= wait <= MAX_RETRY_WAIT_S:
            raise ValueError(f"retry_waits_s entries must be within 0..{MAX_RETRY_WAIT_S}")
    return tuple(raw)


@dataclass(frozen=True)
class FilePolicy:
    folder_structure: str = "{:%Y/%m}"
    file_match_policy: str = "name-id7"
    live_photo_mov_filename_policy: str = "original"
    keep_unicode_in_filenames: bool = True
    threads_num: int = 2

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> FilePolicy:
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown policy keys: {sorted(unknown)}")
        policy = cls(**data)
        if policy.file_match_policy not in FILE_MATCH_POLICIES:
            raise ValueError(f"bad file_match_policy {policy.file_match_policy!r}")
        if policy.live_photo_mov_filename_policy not in LIVE_PHOTO_POLICIES:
            raise ValueError(
                f"bad live_photo_mov_filename_policy {policy.live_photo_mov_filename_policy!r}"
            )
        if policy.threads_num < 1:
            raise ValueError("threads_num must be >= 1")
        if not policy.folder_structure:
            raise ValueError("folder_structure must not be empty")
        return policy


@dataclass(frozen=True)
class JobSpec:
    mode: str
    run_id: str
    account_id: str
    apple_id: str
    region: str
    client_id: str
    cookie_dir: str
    photos_root: str | None = None
    library_zone: str | None = None
    target_dir: str | None = None
    plan_dir: str | None = None
    interactive: bool = False
    renew: bool = False
    full: bool = False
    until_found: int = 50
    recent: int | None = None
    dry_run: bool = False
    mfa_timeout_s: int = 600
    index_wait_s: int = 300
    retry_waits_s: tuple[int, ...] = (10, 30, 60)
    apply_plan: str | None = None
    policy: FilePolicy = field(default_factory=FilePolicy)

    @property
    def is_primary(self) -> bool:
        return self.library_zone in (None, "", PRIMARY_ZONE)

    @property
    def library_rel(self) -> str:
        """Library directory relative to photos_root; used to lay out the trash."""
        if not self.target_dir or not self.photos_root:
            raise ValueError("library_rel needs target_dir and photos_root")
        rel = os.path.relpath(os.path.normpath(self.target_dir), os.path.normpath(self.photos_root))
        if rel == "." or rel.startswith(".."):
            raise ValueError("target_dir must be a directory inside photos_root")
        return rel

    @classmethod
    def load(
        cls, mode: str, run_id: str, job_json: str, apply_plan: str | None = None
    ) -> JobSpec:
        if mode not in MODES:
            raise ValueError(f"bad mode {mode!r}, expected one of {MODES}")
        data = json.loads(job_json)
        if not isinstance(data, dict):
            raise ValueError(f"{JOB_ENV} must be a JSON object")
        policy = FilePolicy.from_dict(data.pop("policy", {}))
        if "retry_waits_s" in data:
            data["retry_waits_s"] = _retry_waits(data["retry_waits_s"])
        for key in ("account_id", "apple_id", "region", "client_id", "cookie_dir"):
            if not data.get(key):
                raise ValueError(f"{JOB_ENV} missing {key}")
        if data["region"] not in REGIONS:
            raise ValueError(f"bad region {data['region']!r}, expected one of {REGIONS}")
        reserved = {"mode", "run_id", "apply_plan", "policy"}
        known = {f.name for f in fields(cls)} - reserved
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown job keys: {sorted(unknown)}")
        spec = cls(mode=mode, run_id=run_id, apply_plan=apply_plan, policy=policy, **data)
        if mode in ("sync", "reconcile"):
            if not spec.target_dir or not spec.photos_root:
                raise ValueError(f"{mode} needs target_dir and photos_root")
            if not os.path.isabs(spec.target_dir) or not os.path.isabs(spec.photos_root):
                raise ValueError("target_dir and photos_root must be absolute paths")
            _ = spec.library_rel  # validates containment
        if mode == "reconcile" and not apply_plan and not spec.plan_dir:
            raise ValueError("reconcile needs plan_dir (or --apply-plan)")
        if not isinstance(spec.renew, bool):
            raise ValueError("renew must be a boolean")
        if spec.renew and (mode != "auth" or not spec.interactive):
            raise ValueError("renew is for interactive auth jobs only")
        return spec

    @classmethod
    def from_env(
        cls,
        mode: str,
        run_id: str,
        apply_plan: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> JobSpec:
        source = os.environ if env is None else env
        raw = source.get(JOB_ENV)
        if not raw:
            raise ValueError(f"{JOB_ENV} is not set")
        return cls.load(mode, run_id, raw, apply_plan)
