#!/usr/bin/env python3
"""Scripted stand-in for `python -m icloud2nas.worker` used by orchestrator tests.

Speaks docs/worker-protocol.md: JSON events on stdout, stderr free-form, exit code last word.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import UTC, datetime
from typing import Any


def emit(event: dict[str, Any]) -> None:
    record = {"ev": event["ev"], "ts": datetime.now(UTC).isoformat(timespec="seconds")}
    record.update({k: v for k, v in event.items() if k != "ev"})
    sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def default_scenario(mode: str, job: dict[str, Any], run_id: str) -> dict[str, Any]:
    if mode == "auth":
        return {"events": [{"ev": "started"}, {"ev": "done", "stats": {}}], "exit": 0}
    if mode == "list":
        return {
            "events": [
                {"ev": "started"},
                {"ev": "libraries", "personal": ["PrimarySync"], "shared": []},
                {"ev": "done", "stats": {"personal": 1, "shared": 0}},
            ],
            "exit": 0,
        }
    if mode == "sync":
        return {
            "events": [
                {"ev": "started"},
                {
                    "ev": "done",
                    "stats": {"downloaded": 1, "trashed": 0, "full": bool(job.get("full"))},
                },
            ],
            "exit": 0,
        }
    plan_file = os.path.join(job.get("plan_dir") or "/tmp", f"{run_id}.txt")
    return {
        "events": [
            {"ev": "started"},
            {
                "ev": "reconcile_plan",
                "remote_total": 10,
                "local_total": 10,
                "extraneous": 0,
                "missing": 0,
                "plan_file": plan_file,
                "fused": False,
            },
            {"ev": "done", "stats": {"trashed": 0, "remote_total": 10, "local_total": 10}},
        ],
        "exit": 0,
    }


def wait_for_code(expected: str) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            code = str(json.loads(line)["code"])
        except (ValueError, KeyError, TypeError):
            code = line
        if code == expected:
            emit({"ev": "mfa_result", "ok": True})
            return
        emit(
            {
                "ev": "mfa_result",
                "ok": False,
                "error": "Failed to verify two-factor authentication code",
            }
        )
    sys.exit(1)  # stdin closed without the right code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--apply-plan")
    args = parser.parse_args()
    job = json.loads(os.environ.get("I2N_JOB", "{}"))
    calls = os.environ.get("FAKE_WORKER_CALLS")
    if calls:
        with open(calls, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "argv": sys.argv[1:],
                        "job": job,
                        "has_password": bool(os.environ.get("I2N_PASSWORD")),
                        "apply_plan": args.apply_plan,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    scenarios = json.loads(os.environ.get("FAKE_WORKER_SCENARIO", "{}"))
    scenario = scenarios.get(args.mode) or default_scenario(args.mode, job, args.run_id)
    for step in scenario["events"]:
        if "wait_code" in step:
            wait_for_code(str(step["wait_code"]))
        elif "sleep" in step:
            time.sleep(float(step["sleep"]))
        elif "stderr" in step:
            sys.stderr.write(str(step["stderr"]) + "\n")
            sys.stderr.flush()
        elif "raw" in step:
            sys.stdout.write(str(step["raw"]) + "\n")
            sys.stdout.flush()
        else:
            event = dict(step)
            if event.get("ev") == "started":
                event.setdefault("mode", args.mode)
                event.setdefault("pid", os.getpid())
                event.setdefault("run_id", args.run_id)
            emit(event)
    return int(scenario.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main())
