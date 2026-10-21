# icloud2nas

Mirror a household's iCloud Photos libraries to a NAS. Multiple Apple IDs, one
copy of each shared library, trash-first deletes, nightly reconcile.

**Status:** worker, orchestrator and HTTP API (Plans 1–2b). Web UI and packaging follow.

Apple offers no third-party authorization: this tool needs each Apple ID's
password and a one-time 2FA code. Credentials never leave your NAS.

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

`icloud2nas-serve` is the one process: scheduler, REST API + SSE on port 8080, notifications and
housekeeping. Dev trial against the real-account probe directory:

    docker compose run --rm -p 8080:8080 \
      -e I2N_DATA_DIR=/probe/data -e I2N_PHOTOS_DIR=/probe/photos \
      dev uv run icloud2nas-serve

`icloud2nas-ctl` is an HTTP client of it (`I2N_URL`, default `http://127.0.0.1:8080`); from a second
terminal on Docker Desktop:

    export I2N_URL=http://host.docker.internal:8080
    docker compose run --rm -e I2N_URL dev uv run icloud2nas-ctl status
    docker compose run --rm -e I2N_URL dev uv run icloud2nas-ctl account add --name 旭刚 --apple-id you@icloud.com --region cn
    docker compose run --rm -e I2N_URL dev uv run icloud2nas-ctl runs

The API is documented in `docs/api.md`, the CLI and scheduling rules in `docs/orchestrator.md`.
