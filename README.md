# icloud2nas

Mirror a household's iCloud Photos libraries to a NAS. Multiple Apple IDs, one
copy of each shared library, trash-first deletes, nightly reconcile.

**Status:** engine/worker only (Plan 1). No orchestrator or web UI yet.

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
