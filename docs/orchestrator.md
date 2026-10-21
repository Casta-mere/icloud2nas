# Orchestrator core (headless)

The orchestrator stores accounts and libraries in SQLite under `I2N_DATA_DIR`, spawns the
worker (`python -m icloud2nas.worker`) as a subprocess per run, and schedules:

- a quick sync every `interval_min` (default 30) per enabled primary library (`until_found=50`),
- a nightly full sync followed by a reconcile at `nightly_at` (default 03:00 local, `TZ`),
- an immediate full sync when a library is first enabled.

Rules: one run per library at a time, one run per account at a time, at most `I2N_MAX_WORKERS`
(default 2) workers; failures back off per account (1 min doubling to 60 min); exit 3 parks the
account as `needs_reauth` until `icloud2nas-ctl account reauth`; exit 4 parks it as
`password_rejected` until `account set-password`; a fused reconcile (exit 6) creates a pending
plan for `plan approve|reject`; a reconcile that saw zero remote assets is treated as a transient
failure, never as a plan; a newer fused reconcile marks older pending plans of the same library
`superseded`, and only a `pending` plan can be approved or rejected. A worker killed by server
shutdown does not count as an account failure (a timeout does). Shared libraries are recorded
but not scheduled (v1.1); a shared zone already recorded by another account is skipped.

`icloud2nas-serve` is the only process that touches the database and spawns workers: it runs
the scheduler, the REST API (`docs/api.md`), the SSE stream, notifications and housekeeping, and
holds an exclusive `flock` on `<data>/daemon.lock` for as long as it lives (released when the
process dies, so a second `icloud2nas-serve` on the same data refuses to start). The CLI is an
HTTP client of that process and takes no lock: stop nothing before a manual run or onboarding.
On SIGTERM uvicorn waits at most 5 s for open connections, so a browser tab holding
`/api/events` cannot block shutdown. A manual request (`run`, `plan approve`) wakes the scheduler
loop and starts on its next iteration instead of waiting up to `tick` (15 s).

Environment: `I2N_DATA_DIR` (/data), `I2N_PHOTOS_DIR` (/photos), `I2N_MAX_WORKERS`,
`I2N_MIN_FREE_GB` (5), `I2N_DEFAULT_INTERVAL_MIN`, `I2N_DEFAULT_NIGHTLY_AT` (HH:MM, `3:00` is
normalised to `03:00`), `I2N_JITTER_S` (`lo,hi` seconds of random delay before the scheduler's first
tick, default `0,300`), `I2N_SECRET_KEY` (optional; else `/data/secret.key` is generated), `TZ`,
`I2N_WORKER_CMD` (tests only).

Each run's worker stderr goes to `<data>/logs/runs/<run_id>.log` (also `runs` → `log_path`).

The SQLite database runs in WAL mode. Never open `<data>/icloud2nas.db` from outside the
container while the daemon runs: on Docker Desktop (macOS/Windows) the bind mount does not share
SQLite's WAL locks with the host, and a host-side reader checkpoints and deletes the WAL under the
daemon's feet, silently losing its writes (runs stay `running` until the next restart sweeps
them). On a Linux NAS the bind mount is a local filesystem and this does not apply, but inspect
the database through `icloud2nas-ctl` anyway.

## CLI

`icloud2nas-ctl` talks to `icloud2nas-serve` over HTTP: `I2N_URL` (default
`http://127.0.0.1:8080`), `I2N_ADMIN_PASSWORD` when an admin password is set (otherwise it asks),
`I2N_CTL_PASSWORD` for the Apple ID password of `account add|set-password` (otherwise it asks).

    icloud2nas-ctl account add --name 旭刚 --apple-id you@icloud.com --region cn [--dir REL]   # asks password + 2FA
    icloud2nas-ctl account list | reauth ID | set-password ID | refresh ID | delete ID [--delete-dir]
    icloud2nas-ctl library list
    icloud2nas-ctl library set ID [--enable|--disable] [--interval 30] [--nightly 03:00] [--dir REL] [--name N]
    icloud2nas-ctl library pause ID | library resume ID
    icloud2nas-ctl run sync ID | run reconcile ID        # now, streaming events; exit code = the run's
    icloud2nas-ctl runs [--library ID] [--limit 20]
    icloud2nas-ctl plan list [--status pending|approved|executed|rejected|superseded|all]
    icloud2nas-ctl plan show ID | plan approve ID | plan reject ID
    icloud2nas-ctl pause | resume | status
    icloud2nas-ctl settings get | settings set KEY VALUE
    icloud2nas-ctl trash list | trash day REL DAY | trash delete REL DAY | trash keep REL DAY PATH…

`account add|reauth|set-password` follow the login on the event stream (prompting for 2FA codes
and re-prompting on a wrong one), then wait for library discovery and print the libraries.
`run` and `plan approve` follow their run until it finishes. `library set --dir` renames the
target directory (the folder is moved when it exists). If discovery fails after a login the cause
is printed; fix it and run `icloud2nas-ctl account refresh ID`.

Errors print `error: <detail>` and exit 1. Ids accept an unambiguous prefix; accounts also accept
the Apple ID or display name, libraries their display name.

Inside the dev container, see the README for running `icloud2nas-serve` with `./.probe` mounted
at `/probe` (`I2N_DATA_DIR=/probe/data I2N_PHOTOS_DIR=/probe/photos`).
