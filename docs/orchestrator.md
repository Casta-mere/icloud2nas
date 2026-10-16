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
failure, never as a plan. Shared libraries are recorded but not scheduled (v1.1).

Environment: `I2N_DATA_DIR` (/data), `I2N_PHOTOS_DIR` (/photos), `I2N_MAX_WORKERS`,
`I2N_MIN_FREE_GB` (5), `I2N_DEFAULT_INTERVAL_MIN`, `I2N_DEFAULT_NIGHTLY_AT`, `I2N_SECRET_KEY`
(optional; else `/data/secret.key` is generated), `TZ`, `I2N_WORKER_CMD` (tests only).

## CLI

    icloud2nas-ctl account add --name 旭刚 --apple-id you@icloud.com --region cn   # asks password + 2FA
    icloud2nas-ctl account list | reauth ID | set-password ID
    icloud2nas-ctl library list
    icloud2nas-ctl library set ID [--enable|--disable] [--interval 30] [--nightly 03:00] [--dir REL] [--name N]
    icloud2nas-ctl run sync ID | run reconcile ID        # now, streaming events
    icloud2nas-ctl runs [--library ID] [--limit 20]
    icloud2nas-ctl plan list [--status pending|executed|rejected] | plan approve ID | plan reject ID
    icloud2nas-ctl daemon                                # the scheduler; stop with SIGTERM

Ids accept an unambiguous prefix; accounts also accept the Apple ID. Inside the dev container:
`docker compose run --rm dev uv run icloud2nas-ctl …` with `./.probe` mounted at `/probe`
(`I2N_DATA_DIR=/probe/data I2N_PHOTOS_DIR=/probe/photos`).
