# icloud2nas

Mirror a household's iCloud Photos libraries to a NAS. Multiple Apple IDs, one
copy of each shared library, trash-first deletes, nightly reconcile.

Apple offers no third-party authorization: this tool needs each Apple ID's
password and a one-time 2FA code. Credentials never leave your NAS.

## Status

What works today:

- **Sync.** Each Apple ID (icloud.com or icloud.com.cn) mirrors its own photo library into a
  folder under `/photos`: a quick sync every 30 minutes, and every night at 03:00 a full sync
  followed by a reconcile. Both are per-library settings.
- **Trash-first deletes.** A file whose photo is gone from iCloud is moved to the library's trash,
  never unlinked, and purged once the retention (30 days by default) runs out; any file can be
  kept instead. A reconcile that would trash a large share of a library stops and waits for your
  approval.
- **Web UI** with five pages: Overview (accounts and recent runs, with a code box when Apple asks
  for a verification code), Accounts (add, rename, change password, delete), Libraries (per-library
  sync settings and the reconcile approval card), Trash (keep or permanently delete) and Settings
  (notifications, sync, retention, admin password). It is bilingual (中文 / English) and works on
  a phone. See [docs/web.md](docs/web.md).
- **Access mode.** On the first visit you choose LAN (no password) or WAN (an admin password).
- **Notifications** through [Apprise](https://github.com/caronc/apprise) URLs.
- **File owners.** Each account's files are written as the NAS user you pick for it, or the whole
  container runs as one user (`PUID`/`PGID`).
- **Image** `ghcr.io/casta-mere/icloud2nas` for linux/amd64 and linux/arm64. Each release tag
  publishes it and a [GitHub Release](https://github.com/Casta-mere/icloud2nas/releases); see
  [docs/release.md](docs/release.md).

Not yet: shared libraries are discovered and recorded, but not synced.

## Quick start

On the NAS, in a folder holding this repository's [`compose.example.yaml`](compose.example.yaml)
and [`.env.example`](.env.example):

    cp compose.example.yaml compose.yaml
    cp .env.example .env        # set PHOTOS_ROOT and TZ
    docker compose up -d        # pulls ghcr.io/casta-mere/icloud2nas:latest

1. Open `http://<nas>:4871` and choose an access mode. **LAN** needs no password, so use it only
   when nothing outside your home network reaches the port. **WAN** asks for an admin password,
   which every visitor then has to enter; choose it for anything reachable from the internet.
2. Accounts → Add account: a display name, the photo folder (relative to `/photos`), its owner
   and the region; then the Apple ID and its password; then the code Apple sends to your trusted
   devices. The account's library is found and its first full sync starts.

[docs/deploy.md](docs/deploy.md) covers what to mount as `/photos`, file owners, updating, pinning
a version, reverse proxies and backing up the secret key.

## Development

Everything runs in a container; only Docker is needed on the host.

    docker compose build dev
    make sync        # resolve + install deps into the dev container's venv (repeat after editing pyproject.toml)
    make test
    make lint
    make typecheck
    make shell       # a shell inside the dev container

Inside the container the package manager is uv (https://docs.astral.sh/uv/): it
creates the venv, resolves dependencies and keeps `uv.lock` pinned.
icloudpd is pinned to its pure-Python wheel via [tool.uv.sources]; a plain pip install on Linux would fetch the binary-only platform wheel, which is not importable.

See `docs/probe.md` for the real-account probe.

## Running the worker by hand

    export I2N_JOB='{"account_id":"acc1","apple_id":"you@icloud.com","region":"cn",
      "client_id":"<uuid>","cookie_dir":"/data/accounts/acc1/cookies",
      "photos_root":"/photos","target_dir":"/photos/you","plan_dir":"/data/plans",
      "interactive":true}'
    export I2N_PASSWORD='...'
    docker compose run --rm -e I2N_JOB -e I2N_PASSWORD dev uv run python -m icloud2nas.worker --mode auth --run-id r1   # answer the 2FA prompt on stdin: {"code":"123456"}
    docker compose run --rm -e I2N_JOB -e I2N_PASSWORD dev uv run python -m icloud2nas.worker --mode list --run-id r2
    docker compose run --rm -e I2N_JOB -e I2N_PASSWORD dev uv run python -m icloud2nas.worker --mode sync --run-id r3
    docker compose run --rm -e I2N_JOB -e I2N_PASSWORD dev uv run python -m icloud2nas.worker --mode reconcile --run-id r4

Events arrive on stdout as JSON lines; icloudpd's log goes to stderr. Exit codes:
0 ok · 1 failed · 3 needs 2FA (non-interactive) · 4 password rejected · 5 Apple unavailable ·
6 reconcile fused (plan written, nothing deleted — re-run with `--apply-plan FILE` to execute).
The full orchestrator contract is in [docs/worker-protocol.md](docs/worker-protocol.md).

## Running the service

`icloud2nas-serve` is the one process: scheduler, REST API + SSE on port 4871, notifications and
housekeeping. Dev trial against the real-account probe directory (the `serve` compose service
mounts `./.probe`; the web UI dev server is on http://localhost:5173, see [docs/web.md](docs/web.md)):

    make web-dev            # serve (API on :4871) + Vite UI on :5173
    docker compose up serve # API only

`icloud2nas-ctl` is an HTTP client of it (`I2N_URL`, default `http://127.0.0.1:4871`); from a second
terminal on Docker Desktop:

    export I2N_URL=http://host.docker.internal:4871
    docker compose run --rm -e I2N_URL dev uv run icloud2nas-ctl status
    docker compose run --rm -e I2N_URL dev uv run icloud2nas-ctl account add --name 小明 --apple-id you@icloud.com --region cn
    docker compose run --rm -e I2N_URL dev uv run icloud2nas-ctl runs

Forgot the admin password? Stop the service and run `icloud2nas-serve --reset-admin-password`
(same data directory); then open the web UI and choose an access mode (LAN or WAN) again.

The API is documented in `docs/api.md`, the CLI and scheduling rules in `docs/orchestrator.md`.
