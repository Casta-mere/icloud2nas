"""icloud2nas-ctl: command line client for the icloud2nas HTTP API (`icloud2nas-serve`)."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, TextIO
from urllib.parse import quote

import httpx

from icloud2nas.ctl_follow import (
    Api,
    CtlError,
    follow_auth,
    follow_run,
    print_libraries,
)
from icloud2nas.ctl_follow import parse_sse as parse_sse  # re-exported for tests

DEFAULT_URL = "http://127.0.0.1:4871"
PASSWORD_ENV = "I2N_CTL_PASSWORD"  # the Apple ID password for `account add|set-password`
PLAN_STATUSES = ("pending", "approved", "executed", "rejected", "superseded", "all")


# -- output ----------------------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M")  # API times are UTC, shown local
    return str(value)


def _row(out: TextIO, *cols: Any) -> None:
    out.write("\t".join(_fmt(c) for c in cols) + "\n")


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


# -- commands --------------------------------------------------------------------------------------


def _segment(text: str) -> str:
    if not text.strip():
        raise CtlError("id must not be empty")
    return quote(text, safe="")


def _account(api: Api, text: str) -> dict[str, Any]:
    account: dict[str, Any] = api.get(f"/api/accounts/{_segment(text)}")
    return account


def _library(api: Api, text: str) -> dict[str, Any]:
    library: dict[str, Any] = api.get(f"/api/libraries/{_segment(text)}")
    return library


def _apple_password(env: Mapping[str, str], ask_password: Callable[[str], str], who: str) -> str:
    return env.get(PASSWORD_ENV) or ask_password(f"Password for {who}: ")


def cmd_account(
    api: Api,
    args: argparse.Namespace,
    env: Mapping[str, str],
    ask_password: Callable[[str], str],
    ask_code: Callable[[str], str],
    out: TextIO,
) -> int:
    if args.action == "add":
        body: dict[str, Any] = {
            "display_name": args.name,
            "apple_id": args.apple_id,
            "region": args.region,
            "password": _apple_password(env, ask_password, args.apple_id),
        }
        if args.dir:
            body["target_dir"] = args.dir

        def start() -> str:
            created = api.send("POST", "/api/accounts", body)
            out.write(f"account {created['id']} created; logging in…\n")
            return str(created["run_id"])

        return follow_auth(api, start, ask_code, out)
    if args.action == "list":
        for a in api.get("/api/accounts"):
            _row(
                out,
                a["id"],
                a["display_name"],
                a["apple_id"],
                a["region"],
                a["status"],
                a["failures"],
                _ts(a["backoff_until"]),
                a["last_error"][:60] if a["last_error"] else None,
            )
        return 0
    account = _account(api, args.id)
    account_id = account["id"]
    if args.action == "refresh":
        result = api.send("POST", f"/api/accounts/{account_id}/refresh")
        print_libraries(out, result["personal"], result["shared"])
        return 0
    if args.action == "delete":
        api.call(
            "DELETE",
            f"/api/accounts/{account_id}",
            params={"keep_dir": "false" if args.delete_dir else "true"},
        )
        out.write(f"account {account_id} deleted\n")
        return 0
    if args.action == "set-password":
        password = _apple_password(env, ask_password, account["apple_id"])

        def start_password() -> str:
            patched = api.send("PATCH", f"/api/accounts/{account_id}", {"password": password})
            return str(patched["run_id"])

        return follow_auth(api, start_password, ask_code, out)
    return follow_auth(
        api,
        lambda: str(api.send("POST", f"/api/accounts/{account_id}/auth")["run_id"]),
        ask_code,
        out,
    )


def cmd_library(api: Api, args: argparse.Namespace, out: TextIO) -> int:
    if args.action == "list":
        accounts = {a["id"]: a["display_name"] for a in api.get("/api/accounts")}
        for lib in api.get("/api/libraries"):
            _row(
                out,
                lib["id"],
                accounts.get(lib["account_id"]),
                lib["display_name"],
                lib["zone_name"],
                lib["target_dir"],
                lib["enabled"],
                lib["status"],
                lib["interval_min"],
                lib["nightly_at"],
                _ts(lib["last_success_at"]),
            )
        return 0
    library_id = _library(api, args.id)["id"]
    if args.action in ("pause", "resume"):
        api.send("POST", f"/api/libraries/{library_id}/{args.action}")
        return 0
    patch: dict[str, Any] = {}
    if args.enable:
        patch["enabled"] = True
    if args.disable:
        patch["enabled"] = False
    if args.interval is not None:
        patch["interval_min"] = args.interval
    if args.nightly is not None:
        patch["nightly_at"] = args.nightly
    if args.name is not None:
        patch["display_name"] = args.name
    if args.dir is not None:
        patch["target_dir"] = args.dir
    if not patch:
        raise CtlError("nothing to set; pass --enable/--disable/--interval/--nightly/--dir/--name")
    api.send("PATCH", f"/api/libraries/{library_id}", patch)
    return 0


def cmd_run(api: Api, args: argparse.Namespace, out: TextIO) -> int:
    library_id = _library(api, args.id)["id"]
    return follow_run(
        api,
        library_id,
        lambda: api.call("POST", f"/api/libraries/{library_id}/{args.mode}").json(),
        out,
    )


def cmd_runs(api: Api, args: argparse.Namespace, out: TextIO) -> int:
    params: dict[str, Any] = {"limit": args.limit}
    if args.library:
        params["library_id"] = _library(api, args.library)["id"]
    for r in api.get("/api/runs", **params):
        _row(
            out,
            r["id"],
            r["mode"],
            r["trigger"],
            r["status"],
            _ts(r["started_at"]),
            _ts(r["ended_at"]),
            r["exit_code"],
            r["error_kind"],
            r["error_message"],
        )
    return 0


def cmd_plan(api: Api, args: argparse.Namespace, out: TextIO) -> int:
    if args.action == "list":
        for p in api.get("/api/plans", status=args.status):
            _row(
                out,
                p["id"],
                p["library_id"],
                p["status"],
                p["extraneous_count"],
                p["plan_file"],
                _ts(p["created_at"]),
            )
        return 0
    plan = api.get(f"/api/plans/{_segment(args.id)}")
    plan_id = plan["id"]
    if args.action == "show":
        out.write(f"plan {plan_id} {plan['status']} library={plan['library_id']}\n")
        out.write(f"{plan['total']} paths ({plan['extraneous_count']} extraneous)\n")
        for path in plan["paths"]:
            out.write(f"{path}\n")
        if plan["total"] > len(plan["paths"]):
            out.write(f"… {plan['total'] - len(plan['paths'])} more in {plan['plan_file']}\n")
        return 0
    if args.action == "reject":
        api.send("POST", f"/api/plans/{plan_id}/reject")
        out.write(f"plan {plan_id} rejected\n")
        return 0
    final: dict[str, str] = {}

    def resolved(topic: str, data: dict[str, Any]) -> int | None:
        if topic == "plan.resolved" and data.get("plan_id") == plan_id:
            final["status"] = data["status"]
            return 0 if data["status"] == "executed" else 1
        return None

    code = follow_run(
        api,
        plan["library_id"],
        lambda: api.call("POST", f"/api/plans/{plan_id}/approve").json(),
        out,
        until=resolved,
    )
    out.write(f"plan {plan_id} {final.get('status', 'unresolved')}\n")
    return code


def cmd_settings(api: Api, args: argparse.Namespace, out: TextIO) -> int:
    if args.action == "get":
        for key, value in sorted(api.get("/api/settings")["values"].items()):
            out.write(f"{key}\t{value}\n")
        return 0
    api.send("PUT", "/api/settings", {args.key: args.value})
    return 0


def cmd_trash(api: Api, args: argparse.Namespace, out: TextIO) -> int:
    if args.action == "list":
        for entry in api.get("/api/trash"):
            for day in entry["days"]:
                _row(out, entry["display_name"], day["day"], day["files"], day["bytes"])
        return 0
    path = f"/api/trash/{quote(args.day, safe='')}"
    if args.action == "day":
        for f in api.get(path, library=args.library):
            _row(out, f["rel"], f["size"])
    elif args.action == "delete":
        deleted = api.call("DELETE", path, params={"library": args.library}).json()["deleted"]
        out.write(f"deleted {deleted}\n")
    else:
        body = {"paths": args.paths}
        kept = api.call("POST", path + "/keep", params={"library": args.library}, json=body)
        for rel in kept.json()["kept"]:
            out.write(f"kept {rel}\n")
    return 0


def cmd_status(api: Api, out: TextIO) -> int:
    for key, value in api.get("/api/status").items():
        out.write(f"{key}\t{value}\n")
    return 0


# -- entry -----------------------------------------------------------------------------------------


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="icloud2nas-ctl")
    sub = p.add_subparsers(dest="cmd", required=True)

    acc = sub.add_parser("account").add_subparsers(dest="action", required=True)
    add = acc.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--apple-id", required=True)
    add.add_argument("--region", choices=["cn", "com"], required=True)
    add.add_argument("--dir", help="target directory under the photos root (default: the name)")
    acc.add_parser("list")
    for name in ("reauth", "set-password", "refresh"):
        acc.add_parser(name).add_argument("id")
    delete = acc.add_parser("delete")
    delete.add_argument("id")
    delete.add_argument("--delete-dir", action="store_true", help="also remove the photo folder")

    lib = sub.add_parser("library").add_subparsers(dest="action", required=True)
    lib.add_parser("list")
    st = lib.add_parser("set")
    st.add_argument("id")
    st.add_argument("--enable", action="store_true")
    st.add_argument("--disable", action="store_true")
    st.add_argument("--interval", type=int)
    st.add_argument("--nightly")
    st.add_argument("--dir")
    st.add_argument("--name")
    for name in ("pause", "resume"):
        lib.add_parser(name).add_argument("id")

    run = sub.add_parser("run")
    run.add_argument("mode", choices=["sync", "reconcile"])
    run.add_argument("id")

    runs = sub.add_parser("runs")
    runs.add_argument("--library")
    runs.add_argument("--limit", type=int, default=20)

    plan = sub.add_parser("plan").add_subparsers(dest="action", required=True)
    pl = plan.add_parser("list")
    pl.add_argument("--status", default="pending", choices=PLAN_STATUSES)
    for name in ("show", "approve", "reject"):
        plan.add_parser(name).add_argument("id")

    for name in ("pause", "resume", "status"):
        sub.add_parser(name)

    settings = sub.add_parser("settings").add_subparsers(dest="action", required=True)
    settings.add_parser("get")
    sset = settings.add_parser("set")
    sset.add_argument("key")
    sset.add_argument("value")

    trash = sub.add_parser("trash").add_subparsers(dest="action", required=True)
    trash.add_parser("list")
    for name in ("day", "delete", "keep"):
        t = trash.add_parser(name)
        t.add_argument("library", metavar="LIBRARY")
        t.add_argument("day")
        if name == "keep":
            t.add_argument("paths", nargs="+", metavar="PATH")
    return p.parse_args(argv)


def main(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    ask_password: Callable[[str], str] = getpass.getpass,
    ask_code: Callable[[str], str] = input,
    out: TextIO = sys.stdout,
    client: httpx.Client | None = None,
) -> int:
    source = os.environ if env is None else env
    args = parse_args(argv)
    owned = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(30.0, read=None))
    api = Api(http, source.get("I2N_URL") or DEFAULT_URL, source, ask_password)
    try:
        return _dispatch(api, args, source, ask_password, ask_code, out)
    except CtlError as exc:
        out.write(f"error: {exc}\n")
        return 1
    except KeyboardInterrupt:
        out.write("interrupted\n")
        return 130
    except EOFError:
        out.write("error: input ended before a value was entered\n")
        return 1
    finally:
        if owned:
            http.close()


def _dispatch(
    api: Api,
    args: argparse.Namespace,
    env: Mapping[str, str],
    ask_password: Callable[[str], str],
    ask_code: Callable[[str], str],
    out: TextIO,
) -> int:
    match args.cmd:
        case "account":
            return cmd_account(api, args, env, ask_password, ask_code, out)
        case "library":
            return cmd_library(api, args, out)
        case "run":
            return cmd_run(api, args, out)
        case "runs":
            return cmd_runs(api, args, out)
        case "plan":
            return cmd_plan(api, args, out)
        case "settings":
            return cmd_settings(api, args, out)
        case "trash":
            return cmd_trash(api, args, out)
        case "status":
            return cmd_status(api, out)
        case "pause" | "resume":
            paused = api.send("POST", f"/api/{args.cmd}")["paused"]
            out.write(f"paused: {str(paused).lower()}\n")
            return 0
    raise CtlError(f"unknown command {args.cmd}")  # pragma: no cover - argparse guards this


def cli() -> None:
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    cli()
