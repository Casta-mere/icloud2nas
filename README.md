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
