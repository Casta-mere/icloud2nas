"""Build and type-check the I2N_JOB document handed to a worker (docs/worker-protocol.md)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from icloud2nas.engine.job import JobSpec
from icloud2nas.orchestrator.config import Config
from icloud2nas.orchestrator.store import repo
from icloud2nas.orchestrator.store.models import Account, Library

PRIMARY_ZONE = "PrimarySync"
QUICK_UNTIL_FOUND = 50
MFA_TIMEOUT_S = 600
INDEX_WAIT_S = 300

JOB_FIELD_TYPES: dict[str, type | tuple[type, ...]] = {
    "account_id": str,
    "apple_id": str,
    "region": str,
    "client_id": str,
    "cookie_dir": str,
    "photos_root": str,
    "library_zone": (str, type(None)),
    "target_dir": str,
    "plan_dir": str,
    "interactive": bool,
    "full": bool,
    "until_found": int,
    "recent": (int, type(None)),
    "dry_run": bool,
    "mfa_timeout_s": int,
    "index_wait_s": int,
    "policy": dict,
}
POLICY_FIELD_TYPES: dict[str, type] = {
    "folder_structure": str,
    "file_match_policy": str,
    "live_photo_mov_filename_policy": str,
    "keep_unicode_in_filenames": bool,
    "threads_num": int,
}
_POLICY_SETTING_PARSERS: dict[str, Any] = {
    "folder_structure": str,
    "file_match_policy": str,
    "live_photo_mov_filename_policy": str,
    "keep_unicode_in_filenames": lambda v: v.lower() in ("1", "true", "yes"),
    "threads_num": int,
}


def policy_from_settings(s: Session) -> dict[str, Any]:
    policy: dict[str, Any] = {}
    for key, parse in _POLICY_SETTING_PARSERS.items():
        raw = repo.get_setting(s, key, "")
        if raw != "":
            policy[key] = parse(raw)
    return policy


def build_job(
    config: Config,
    account: Account,
    library: Library | None,
    *,
    interactive: bool,
    full: bool = False,
    dry_run: bool = False,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job: dict[str, Any] = {
        "account_id": account.id,
        "apple_id": account.apple_id,
        "region": account.region,
        "client_id": account.client_id,
        "cookie_dir": str(config.cookie_dir(account.id)),
        "photos_root": str(config.photos_dir),
        "plan_dir": str(config.plans_dir),
        "interactive": interactive,
        "full": full,
        "dry_run": dry_run,
        "until_found": QUICK_UNTIL_FOUND,
        "mfa_timeout_s": MFA_TIMEOUT_S,
        "index_wait_s": INDEX_WAIT_S,
    }
    if library is not None:
        job["target_dir"] = str(config.photos_dir / library.target_dir)
        job["library_zone"] = None if library.zone_name == PRIMARY_ZONE else library.zone_name
    if policy:
        job["policy"] = dict(policy)
    return job


def _check_types(values: dict[str, Any], schema: dict[str, Any], where: str) -> None:
    for key, value in values.items():
        expected = schema.get(key)
        if expected is None:
            raise ValueError(f"{where}: unknown field {key!r}")
        if isinstance(value, bool) and expected is int:
            raise ValueError(f"{where}: {key} must be int, got bool")
        if not isinstance(value, expected):
            raise ValueError(f"{where}: {key} must be {expected}, got {type(value).__name__}")


def validate_job(mode: str, job: dict[str, Any]) -> None:
    """Type-check (spec §3.3a hard requirement 5) and then run the worker's own validation."""
    _check_types(job, JOB_FIELD_TYPES, "job")
    if "policy" in job:
        _check_types(job["policy"], POLICY_FIELD_TYPES, "policy")
    JobSpec.load(mode, "validate", json.dumps(job))
