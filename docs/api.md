# HTTP API

`icloud2nas-serve` (port `I2N_PORT`, default 4871) serves everything under `/api`, plus `/healthz`
and `/metrics`. Bodies are JSON unless noted. The web UI and `icloud2nas-ctl` are clients of
this API; nothing else touches the database or the workers.

Every mutating request under `/api` (`POST`, `PUT`, `PATCH`, `DELETE`, the session routes
included) must carry `Content-Type: application/json` (any `charset`), even when it has no
body: send `{}`. Anything else is answered `415 {"error": "unsupported media type", "detail":
"send application/json"}` before authentication or routing. A cross-site HTML form or a
`no-cors` fetch cannot send that header, so this doubles as the CSRF guard.

## Auth

The API runs in one of three access modes, reported by `GET /api/session`:

- `setup`: nobody has chosen yet (a fresh install, or after `--reset-admin-password`). Every
  protected route answers `428 {"detail": {"error": "setup required", "detail": "choose LAN or WAN
  mode in the web UI first"}}`.
- `lan`: no password; every route is open to whoever can reach the port.
- `wan`: an admin password is set; every route except the open ones requires the `i2n_session`
  cookie (signed, HTTP-only, 30 days), from anywhere, the LAN included. Without a valid cookie
  protected routes answer `401`.

The mode is derived, not stored: a stored password means `wan`, else a chosen LAN means `lan`,
else `setup`. An install from before access modes therefore reads as `wan` when it had a password
and as `setup` when it had none; it never becomes `lan` by itself. `access_mode` is an internal
setting and cannot be written through `PUT /api/settings`.

Leaving `setup`: `POST /api/setup` (once; later calls are `409`). `{"mode": "lan"}` chooses LAN.
`{"mode": "wan", "password": "..."}` sets the password and answers with the session cookie, so
the caller is logged in. Switching later: `PUT /api/settings {"admin_password": "..."}` in `lan`
makes it `wan`; `{"admin_password": ""}` in `wan` makes it `lan` (never back to `setup`).
Changing or clearing the password invalidates earlier sessions.

Open routes, whatever the mode: `/healthz`, `/metrics`, `GET`/`POST`/`DELETE /api/session`,
`POST /api/setup`, and the web UI's static files. Only HTTP is gated: the scheduler,
housekeeping and notifier keep working in every mode, `setup` included.

TLS is the reverse proxy's job. A forgotten password is cleared offline with
`icloud2nas-serve --reset-admin-password` (see `docs/orchestrator.md`), which leads back to
`setup`.

`icloud2nas-ctl` reads the base URL from `I2N_URL` (default `http://127.0.0.1:4871`). On a 401
(or before opening the event stream) it logs in with `I2N_ADMIN_PASSWORD`, or asks for the
password, then retries once. In `setup` it prints the `428` text (choose LAN or WAN mode in the
web UI first) and exits 1.

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/api/session` | | `{mode: setup\|lan\|wan, auth_required, authenticated, remote}`; `auth_required` is `mode == "wan"`; `remote` is true when the request carries `Forwarded`, `X-Forwarded-For` or `X-Real-IP`, or comes from a public address (a hint for a warning, nothing is gated on it) | |
| POST | `/api/session` | `{password}` | 204 + `Set-Cookie: i2n_session` | 400 not in `wan` (no cookie is set), 401 wrong password |
| DELETE | `/api/session` | | 204, cookie cleared | |
| POST | `/api/setup` | `{mode: lan\|wan, password?}` (`password` required for `wan`) | 204; `wan` also sets `Set-Cookie: i2n_session` | 400 `wan` without a password, 409 already set up (not in `setup`) |

## Errors

Failures are `{"error": "<kind>", "detail": "<text>"}`: `400` invalid input (`ValueError`),
`401` unauthorized, `404` unknown id, `409` busy / already exists / already set up, `415` a
mutating request without `Content-Type: application/json`, `422` malformed body (FastAPI
validation), `428` setup required (the install is in `setup` mode; body as shown under Auth),
`500` cannot decrypt stored passwords, `502` the worker failed.

## Ids

Wherever a path takes an account, library, run or plan id, an unambiguous prefix works too;
accounts also accept their Apple ID or display name, libraries their display name. An
ambiguous prefix is a `400`, no match a `404`.

## System

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/healthz` | | `{ok: true}` (open) | 503 `{ok: false, detail}` database or scheduler down |
| GET | `/metrics` | | Prometheus text (open); library series are labelled `library_id` and `library` (display name) | |
| GET | `/api/status` | | `{paused, space_low, workers_running, version, started_at, update}`; `version` is the running version, `update` the last update check (below; cached, no network) | |
| POST | `/api/pause` | | `{paused: true}`; stops scheduled runs, manual requests still run | |
| POST | `/api/resume` | | `{paused: false}` | |
| POST | `/api/version/check` | | the `update` object after a fresh check; joins a check already running, at most ~30 s; failures are reported in `error`, not as HTTP errors | |

Update object: `{latest, update_available, checked_at, error, compare_url}`. `latest` is the
highest plain `X.Y.Z` tag of `ghcr.io/casta-mere/icloud2nas` (null before any check or when there
is none); `update_available` is `latest > version` compared numerically (a `version` that isn't
plain `X.Y.Z` counts as `0.0.0`); `checked_at` is when the last check finished (ISO time, or
null); `error` is a short reason the last check failed (`timed out`, `couldn't reach ghcr.io`,
`unexpected response (HTTP 403)`, ...) or null; `compare_url` is
`https://github.com/Casta-mere/icloud2nas/compare/v<version>...v<latest>` when an update is
available and `version` is plain `X.Y.Z`, else null. A failed check keeps `latest`,
`update_available` and `compare_url` from the last successful one. The service checks about a
minute after start and then daily while `update_check` is on and the access mode isn't `setup`.

## Accounts

Account object: `id, display_name, apple_id, region, status, failures, backoff_until,
last_auth_ok_at, last_error, created_at, primary_dir, library_ids, owner_uid, owner_gid,
owner_name`. `status` is one of `new, awaiting_code, ok, needs_reauth, password_rejected`.

File owner (spec §16.3): `owner_uid`/`owner_gid` are the NAS user and group the account's workers
run as, and that the folders the service creates for its libraries (`.icloud2nas/…`, new parent
folders of a moved library, `.icloud2nas-deleted`, the account's cookie and plan folders) are
chowned to. `owner_name` is the host user name for `owner_uid` (from `/host/etc/passwd`), else
null. When the service runs as root and a new account names no owner, the owner of the library
folder's nearest existing ancestor (never above the photos root) is stored. When it does not run
as root (single-user mode, `PUID`/`PGID` set) owners are still accepted, stored and returned, but
ignored: every worker runs as the service's own user and nothing is chowned. A changed owner
takes effect at the next worker start; existing library files are not re-chowned.

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/api/accounts` | | `[account]` | |
| POST | `/api/accounts` | `{display_name, apple_id, region: cn\|com, password, target_dir?, owner_uid?, owner_gid?}` (owner: both or neither, integers >= 0) | 201 `{id, run_id}`; the interactive login starts at once and libraries are discovered after a successful login | 400 invalid, 409 Apple ID exists |
| GET | `/api/accounts/{id}` | | account | 404 |
| PATCH | `/api/accounts/{id}` | `{display_name?, password?, owner_uid?, owner_gid?}` | account; with `password` it re-authenticates and adds `run_id` | 400 (incl. only one of `owner_uid`/`owner_gid`), 404, 409 busy |
| DELETE | `/api/accounts/{id}?keep_dir=true\|false` | | 204; `keep_dir=false` renames each library folder to `<parent>/.icloud2nas-deleted/<name>-<YYYY-MM-DD>` (rename only, all or none) and records it in `deleted_dirs`; it is removed for good once the trash retention expires | 400 a folder is on another filesystem / a mount point / cannot be renamed (nothing moved, account kept), 404, 409 busy |
| POST | `/api/accounts/{id}/auth` | | 202 `{run_id}` (re-login, 2FA again) | 404, 409 busy |
| POST | `/api/accounts/{id}/code` | `{code}` | 204 | 409 no login is waiting for a code |
| POST | `/api/accounts/{id}/refresh` | | `{personal: [zone], shared: [zone]}`; re-discovers libraries without a login | 404, 409 busy, 502 worker failed |

## Host users

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/api/host-users?path=<rel>` | | `{privileged, users: [{name, uid, gid, group, home}], suggested: {uid, gid} \| null}` | 400 `path` absolute or escaping the photos root |

`users` are the host's human users (uid 1000–59999, home under `/home`) read from the read-only
`/host/etc` mount (`I2N_HOST_ETC`); `[]` when it is not mounted. `group` is the name of the
primary group (the gid as text when `/host/etc/group` lacks it). `privileged` is false in
single-user mode (the UI then hides the owner select). `suggested` is the default owner for a
library folder `path` (photos-relative): the owner of its nearest existing ancestor, never above
the photos root; null without `path`.

## Libraries

Library object: `id, account_id, kind (personal|shared), zone_name, display_name, target_dir,
enabled, status (ok|paused|space_paused|dir_missing), interval_min, nightly_at, last_run_at,
last_success_at, first_full_at, stats`. `dir_missing`: the library has completed a full sync but
its folder is gone; it never runs (manual sync/reconcile answer 409
`{"error":"busy","detail":"library folder is missing"}`) and returns to its previous status on
its own once the folder exists again.

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/api/libraries` | | `[library]` | |
| GET | `/api/libraries/{id}` | | library | 404 |
| PATCH | `/api/libraries/{id}` | `{display_name?, target_dir?, enabled?, interval_min?, nightly_at?}` | library; a changed `target_dir` moves the folder | 400 (absolute dir, bad `HH:MM`, dir taken), 404, 409 a run is active |
| POST | `/api/libraries/{id}/sync` | | 202 `{queued: true}`; a full sync, ahead of scheduled work, ignoring pause | 400 disabled / account not ok, 409 space low |
| POST | `/api/libraries/{id}/reconcile` | | 202 `{queued: true}` | same |
| POST | `/api/libraries/{id}/pause` | | library | 404 |
| POST | `/api/libraries/{id}/resume` | | library | 404 |

## Runs

Run object: `id, account_id, library_id, mode, trigger, status, started_at, ended_at,
exit_code, stats, error_kind, error_message, plan_file`.

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/api/runs?library_id=&account_id=&limit=` | | `[run]`, newest first; `limit` 1-500, default 50 | 404 unknown filter id |
| GET | `/api/runs/{id}` | | run | 404 |
| GET | `/api/runs/{id}/log` | | `text/plain`, the last 1 MiB of the worker log | 404 no log |

## Plans

Plan object: `id, library_id, run_id, created_at, extraneous_count, plan_file, status
(pending|approved|executed|rejected|superseded)`.

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/api/plans?status=` | | `[plan]`; `status` is a plan status or `all`, default `pending` | 400 |
| GET | `/api/plans/{id}` | | plan plus `paths` (first 1000) and `total` | 404 |
| POST | `/api/plans/{id}/approve` | | 202 plan (`approved`); the apply run follows, then `plan.resolved` | 400 not pending, 409 busy |
| POST | `/api/plans/{id}/reject` | | plan | 400 not pending |

## Trash

Each library keeps its own trash and kept area inside its folder:
`<target_dir>/.icloud2nas/trash/<YYYY-MM-DD>/<rel>` and `<target_dir>/.icloud2nas/kept/<rel>`.
The routes are keyed by library (`library=` takes an id, an unambiguous id prefix or the display
name). A Plan 2 layout (`/photos/.trash/<rel>/…`, `/photos/kept/<rel>/…`) is moved into the
libraries at startup; trash of unknown folders is left in place and logged.

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/api/trash` | | `[{library_id, display_name, target_dir, days: [{day, files, bytes}]}]`, one entry per library | |
| GET | `/api/trash/{day}?library=` | | `[{rel, size}]` | 400 bad day / no such day, 404 unknown library, 422 `library` missing |
| DELETE | `/api/trash/{day}?library=` | | `{deleted}` files removed for good | 400, 404, 409 the library is running |
| POST | `/api/trash/{day}/keep?library=` | `{paths}` | `{kept: [path]}`; moved to `<target_dir>/.icloud2nas/kept/` | 400 (path escapes the day), 404, 409 the library is running |

## Settings and notifications

| Method | Path | Body | Response | Errors |
|---|---|---|---|---|
| GET | `/api/settings` | | `{values, admin_password_set, public_url_fallback}`; the fallback is the origin of the last authorised browser page load from a non-loopback host, used for notification links while `public_url` is empty | |
| PUT | `/api/settings` | `{<key>: <value>, ..., admin_password?}`; values are strings (booleans `1`/`0`); `admin_password: ""` clears it and switches to `lan`, a non-empty one switches `lan` to `wan` | same as GET | 400 unknown key, bad value, internal key, null |
| POST | `/api/notify/test` | | `{sent}` | 400 `apprise_urls` empty |

Keys: `max_workers, min_free_gb, threads_per_worker, trash_retention_days, log_retention_runs,
file_match_policy, live_photo_mov_filename_policy, keep_unicode_in_filenames, folder_structure,
paused, apprise_urls, public_url, update_check`, plus the notification event switches.
`update_check` (boolean, default on; preset with `I2N_UPDATE_CHECK` while unset) turns the
automatic update checks on or off; `POST /api/version/check` works either way.

## Events

`GET /api/events` is a server-sent event stream. The first frame is `event: hello`
(`{server_time}`); `: ping` comments arrive every 15 s. Each frame is
`event: <topic>\ndata: <json>\n\n`. A slow client loses its oldest events, so clients should
treat events as hints and re-read the resource when in doubt.

| Topic | Payload |
|---|---|
| `run.started` | `run_id, account_id, library_id, mode, trigger` |
| `run.event` | `run_id, event` (a worker event, e.g. `{ev: "need_mfa"}`) |
| `run.finished` | `run_id, status, library_id, account_id, mode, trigger, exit_code, first_full, account_failures` |
| `account.status` | `account_id, status` |
| `account.updated` | `account_id` (and `deleted: true` after a delete) |
| `library.updated` | `library_id` |
| `plan.pending` | `plan_id, extraneous, library_id` |
| `plan.resolved` | `plan_id, library_id, status` |
| `space.low`, `space.ok` | `min_free_gb` |
| `paused` | `paused` |
| `settings.updated` | `keys` |
| `version.checked` | none (`{}`); an update check finished, re-read `/api/status` |

`mode` is `auth`, `list`, `sync`, `reconcile`; `trigger` is `onboarding`, `reauth`, `schedule`,
`nightly`, `chain` (the reconcile that follows a successful nightly sync) or `manual`. The CLI relies on `account.status` (`awaiting_code`), `run.event`
(`mfa_result`), `run.finished` and `plan.resolved`.
