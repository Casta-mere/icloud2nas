#!/usr/bin/env python3
"""Real-account probe for icloud2nas (spec §14). Run it by hand; it never runs in CI.

    docker compose run --rm dev uv run python scripts/probe.py --apple-id you@icloud.com --region cn
    docker compose run --rm dev uv run python scripts/probe.py --compare \
/probe/report-a.json /probe/report-b.json

Steps per account: interactive login (asks for the 2FA code), list libraries, dry-run download
of the N most recent assets, dry-run sync of the first shared zone (if any), dry-run full
listing via reconcile (times it). Nothing is deleted: every mode runs with dry_run=true and the
download goes to <workdir>/photos/<local-part>.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

NAMESPACE = uuid.UUID("6b1f6b3e-8d4e-4c7a-9f0e-1c10ad2a5000")


def build_env(
    apple_id: str,
    region: str,
    workdir: Path,
    *,
    mode_extras: dict[str, Any] | None = None,
) -> dict[str, str]:
    workdir = Path(workdir)
    local_part = apple_id.split("@", 1)[0]
    client_id = str(uuid.uuid5(NAMESPACE, apple_id))
    job: dict[str, Any] = {
        "account_id": f"probe-{local_part}",
        "apple_id": apple_id,
        "region": region,
        "client_id": client_id,
        "cookie_dir": str(workdir / "cookies" / apple_id),
        "photos_root": str(workdir / "photos"),
        "target_dir": str(workdir / "photos" / local_part),
        "plan_dir": str(workdir / "plans"),
        "interactive": True,
        "dry_run": True,
    }
    job.update(mode_extras or {})
    env = dict(os.environ)
    env["I2N_JOB"] = json.dumps(job)
    env["CLIENT_ID"] = client_id
    return env


def run_worker(
    mode: str, env: dict[str, str], *, log_path: Path
) -> tuple[int, list[dict[str, Any]]]:
    """Spawn the worker, relay 2FA prompts to the terminal, return (exit code, events)."""
    cmd = [sys.executable, "-m", "icloud2nas.worker", "--mode", mode, "--run-id", f"probe-{mode}"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            cmd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True
        )
        assert proc.stdout is not None and proc.stdin is not None
        for line in proc.stdout:
            try:
                event = json.loads(line)
                ev = str(event["ev"])
            except (ValueError, KeyError, TypeError):
                print(f"  [raw] {line.rstrip()}")  # not an event: stray output from the worker
                continue
            events.append(event)
            fields = {k: v for k, v in event.items() if k not in ("ev", "ts")}
            print(f"  [{ev}] {json.dumps(fields, ensure_ascii=False)}")
            if ev == "need_mfa":
                code = input("  Apple sent a 2FA code to your trusted devices. Enter it: ").strip()
                proc.stdin.write(json.dumps({"code": code}) + "\n")
                proc.stdin.flush()
        proc.stdin.close()
        rc = proc.wait()
    return rc, events


def compare(a: Path, b: Path) -> list[str]:
    ra = json.loads(Path(a).read_text(encoding="utf-8"))
    rb = json.loads(Path(b).read_text(encoding="utf-8"))
    common = sorted(set(ra.get("shared", [])) & set(rb.get("shared", [])))
    print(f"{ra['apple_id']} shared: {ra.get('shared')}")
    print(f"{rb['apple_id']} shared: {rb.get('shared')}")
    print("common zone names:", common or "NONE — dedupe by zone name will not work")
    return common


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apple-id")
    parser.add_argument("--region", choices=["cn", "com"], default="cn")
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path("/probe") if Path("/probe").is_dir() else Path.home() / "i2n-probe",
    )
    parser.add_argument("--recent", type=int, default=20)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("A.json", "B.json"))
    args = parser.parse_args(argv)
    if args.compare:
        compare(*args.compare)
        return 0
    if not args.apple_id:
        parser.error("--apple-id is required unless --compare is given")
    workdir = args.workdir.expanduser()
    env = build_env(args.apple_id, args.region, workdir)
    env["I2N_PASSWORD"] = getpass.getpass(f"Password for {args.apple_id}: ")
    log = workdir / "logs" / f"{args.apple_id}.log"
    report: dict[str, Any] = {"apple_id": args.apple_id, "region": args.region}

    print("1/5 login")
    rc, _ = run_worker("auth", env, log_path=log)
    if rc != 0:
        print(f"login failed with exit code {rc}; see {log}")
        return rc

    print("2/5 list libraries")
    rc, events = run_worker("list", env, log_path=log)
    libs = next((e for e in events if e["ev"] == "libraries"), None)
    if rc != 0 or libs is None:
        print(f"list failed with exit code {rc}; see {log}")
        return rc or 1
    report["personal"], report["shared"] = libs["personal"], libs["shared"]

    print(f"3/5 dry-run download of the {args.recent} most recent assets")
    sync_env = build_env(
        args.apple_id, args.region, workdir, mode_extras={"recent": args.recent}
    ) | {"I2N_PASSWORD": env["I2N_PASSWORD"]}
    rc, _ = run_worker("sync", sync_env, log_path=log)
    report["sync_exit"] = rc
    print(f"  exit {rc}; filenames icloudpd would write are in {log} (grep 'DRY RUN')")

    if report["shared"]:
        zone = report["shared"][0]
        print(f"4/5 dry-run download from shared zone {zone} (also queries Recently Deleted)")
        shared_env = build_env(
            args.apple_id,
            args.region,
            workdir,
            mode_extras={
                "recent": args.recent,
                "library_zone": zone,
                "target_dir": str(workdir / "photos" / "shared" / zone),
            },
        ) | {"I2N_PASSWORD": env["I2N_PASSWORD"]}
        rc, _ = run_worker("sync", shared_env, log_path=log)
        report["shared_sync_exit"] = rc
        report["shared_zone_probed"] = zone
        print(f"  exit {rc}")
    else:
        print("4/5 no shared library on this account; skipping the shared-zone probe")

    print("5/5 dry-run full listing (reconcile) — timing it")
    started = time.monotonic()
    rc, events = run_worker("reconcile", env, log_path=log)
    report["reconcile_exit"] = rc
    plan = next((e for e in events if e["ev"] == "reconcile_plan"), None)
    report["listing_seconds"] = round(time.monotonic() - started, 1)
    report["remote_total"] = plan["remote_total"] if plan else None
    print(f"  exit {rc}; remote_total={report['remote_total']} in {report['listing_seconds']}s")

    out = workdir / f"report-{args.apple_id}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report written to {out}")
    steps = ("sync_exit", "shared_sync_exit", "reconcile_exit")
    failed = [k for k in steps if k in report and report[k] != 0]
    if failed:
        print(f"probe FAILED: {', '.join(failed)} non-zero; see {log}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
