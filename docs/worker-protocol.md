# Worker protocol

Contract between the orchestrator and one `icloud2nas.worker` process. One process is one job
(`auth`, `list`, `sync` or `reconcile`); events go out on stdout, the result is the exit code.

## Invocation

    python -m icloud2nas.worker --mode {auth|list|sync|reconcile} --run-id RUN_ID [--apply-plan FILE]

- `--mode`: one of `auth`, `list`, `sync`, `reconcile`.
- `--run-id`: opaque string; echoed in `started`, and names the reconcile plan file (`<run_id>.txt`).
- `--apply-plan FILE`: `reconcile` only. Absolute path to an approved plan file; the worker trashes
  exactly the files listed, with no login and no listing.

### Environment

| Variable | Meaning |
| --- | --- |
| `I2N_JOB` | JSON object, see below. Required. |
| `I2N_PASSWORD` | Apple ID password. Required unless the run is `reconcile --apply-plan`. |
| `CLIENT_ID` | Set by the worker from `client_id` in the job; do not rely on setting it yourself. |

Missing or invalid `I2N_JOB` / `I2N_PASSWORD` produces `error kind=config`, exit 1, and no `started`.

### `I2N_JOB` fields

Unknown keys are rejected (`config` error).

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| `account_id` | string | required | |
| `apple_id` | string | required | |
| `region` | `"cn"` or `"com"` | required | |
| `client_id` | string (UUID) | required | stable per Apple ID |
| `cookie_dir` | string | required | created by the worker (parents included) |
| `photos_root` | absolute path | null | required for `sync`/`reconcile` |
| `library_zone` | string | null | null, `""` or `PrimarySync` = personal library; otherwise a shared zone name |
| `target_dir` | absolute path | null | required for `sync`/`reconcile`; must be inside `photos_root` and not equal to it |
| `plan_dir` | string | null | required for `reconcile` unless `--apply-plan` |
| `interactive` | bool | false | true: wait for a 2FA code on stdin; false: exit 3 when 2FA is needed |
| `full` | bool | false | sync: scan the whole library instead of stopping early |
| `until_found` | int | 50 | sync: stop after this many consecutive already-downloaded assets |
| `recent` | int | null | sync: only the N most recent assets |
| `dry_run` | bool | false | nothing is written or moved |
| `mfa_timeout_s` | int | 600 | interactive only: give up waiting for a code after this long |
| `index_wait_s` | int | 300 | list/sync/reconcile: how long to wait for Apple's photo-library web index (`CheckIndexingState`) to report FINISHED before failing with `error kind=not_activated`; Apple starts an index job on each new session and it takes 30 s to a few minutes |
| `policy` | object | see below | |

`policy` (`FilePolicy`; unknown keys rejected):

| Field | Type | Default |
| --- | --- | --- |
| `folder_structure` | string | `"{:%Y/%m}"` |
| `file_match_policy` | `"name-id7"` or `"name-size-dedup-with-suffix"` | `"name-id7"` |
| `live_photo_mov_filename_policy` | `"original"` or `"suffix"` | `"original"` |
| `keep_unicode_in_filenames` | bool | true |
| `threads_num` | int >= 1 | 2 |

## stdin

Used only by interactive runs. Each line is either `{"code": "123456"}` or a bare 6-digit code.
A code is accepted only while the worker is waiting for one (an early code gets up to 5 s of grace
for the prompt to appear); otherwise it is dropped with a `warning`. Other lines are ignored with a
`warning`.

Keep stdin OPEN for interactive runs. EOF ends the feeder thread; after that only the watchdog
(`mfa_timeout_s`) can end the wait. Non-interactive runs may close stdin.

## stdout events

One JSON object per line, each with `ev` and `ts` (ISO 8601 UTC). After `started` the worker
redirects its own `sys.stdout` to stderr, so stray prints cannot corrupt the stream.

| `ev` | Fields | When |
| --- | --- | --- |
| `started` | `mode`, `pid`, `run_id` | first event, after the job parsed |
| `need_mfa` | none | Apple wants a 2FA code (non-interactive: followed by exit 3) |
| `mfa_result` | `ok`, `error?` | after a code was submitted; `ok=false` carries the reason and the wait restarts |
| `libraries` | `personal: [str]`, `shared: [str]` | `list` mode |
| `progress` | sync: `downloaded`, `scanned`, `total`, `last`; reconcile: `scanned` | sync every 60 s; reconcile every 1000 assets. `total` is 0 under `until_found` |
| `reconcile_plan` | `remote_total`, `local_total`, `extraneous`, `missing`, `plan_file`, `fused` | after listing, before any deletion |
| `done` | `stats` | success, see below |
| `error` | `kind`, `message` | failure |
| `warning` | `message` | non-fatal oddity |

`done.stats` per mode:

- `auth`: `{}`
- `list`: `{personal, shared}` (counts)
- `sync`: `{downloaded, trashed, full}`
- `reconcile`: `{trashed, remote_total, local_total}`; with `--apply-plan`: `{trashed, planned}`

`error.kind` is one of `config`, `password`, `auth`, `mfa_timeout`, `ratelimit`, `unavailable`,
`network`, `not_activated`, `api`, `icloudpd`, `unknown`. Mapping: login rejected -> `password`
(4); MFA failure -> `auth`; service unavailable -> `unavailable` (5); connection error ->
`network` (5); HTTP 429 or `ACCESS_DENIED` throttling -> `ratelimit` (5); service not activated ->
`not_activated`; other API errors -> `api`; a failed download phase with no recorded exception ->
`icloudpd`; anything else -> `unknown`. Download-phase failures are classified from the exception
icloudpd logged, so they carry the same kinds and exit codes as login-phase failures.

Parser rules: ignore unknown events and unknown fields; ignore `warning` (informational only).

### Events that can be absent

- `error kind=config` is emitted with no `started`.
- Exit 3 (need 2FA, non-interactive) and exit 6 (fused) end without `done`.
- `reconcile_plan` is emitted before a fused exit (6); that is how the orchestrator gets the plan file.
- An unhandled crash or kill may leave no terminal event at all.

## stderr

icloudpd's and pyicloud's log (including `[DRY RUN]` lines and tracebacks). Keep it as the run log.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | ok (`done` emitted) |
| 1 | failed (see `error`) |
| 3 | 2FA required, non-interactive run |
| 4 | password rejected |
| 5 | Apple unavailable, rate limited or network error: retry later |
| 6 | reconcile fused: plan written, nothing deleted |

The exit code is authoritative; events are explanatory. If they disagree, trust the exit code.

## Counting semantics

- `downloaded` counts files, not assets (a Live Photo is two) and also increments in dry-run.
- `trashed` counts moves to the trash, or would-be moves in dry-run.

## Orchestrator requirements

- Serialise jobs per library: a `sync` and a `reconcile` for the same library must never overlap,
  and jobs for the same Apple ID must run one at a time.
- Reject any `target_dir` that is a path prefix of another library's `target_dir`. Reconcile walks
  the whole `target_dir`; a personal library named `shared` would otherwise trash the shared
  libraries under it.
- Plan files live under `plan_dir` and are named `<run_id>.txt` (one relative path per line).
  Pass the absolute path to `--apply-plan` once approved.
- The worker creates `cookie_dir` (parents included); the orchestrator need not.
- A fuse with `remote_total == 0` means an empty remote listing, almost certainly an API hiccup:
  treat it as "retry later", never "approve".
- Implemented by `icloud2nas.orchestrator` (Plan 2a): per-library and per-account locks in `scheduler.py`, nesting rule in `store/repo.validate_target_dir`, type checks in `jobs.validate_job`.
