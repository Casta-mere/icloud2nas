# Real-account probe

Run before the first deployment and before every icloudpd version bump. It needs a person
holding the trusted iPhone for each Apple ID.

    docker compose run --rm dev uv run python scripts/probe.py --apple-id <id-1> --region cn
    docker compose run --rm dev uv run python scripts/probe.py --apple-id <id-2> --region cn
    docker compose run --rm dev uv run python scripts/probe.py --compare /probe/report-<id-1>.json /probe/report-<id-2>.json

`/probe` inside the container is `./.probe` on the host (git-ignored); the worker's stdin is
attached, so the 2FA prompt works like any terminal program.

What it verifies (spec §14):

1. `region=cn` login + one 2FA round trip works.
2. Both accounts list the same zone name for their shared library (dedupe assumption).
3. Dry-run download of the 20 most recent assets: check the `[DRY RUN]` lines in the log for
   the `YYYY/MM/NAME_<7 chars>.EXT` layout.
4. Full listing time and asset count per account (reconcile cost).

Everything runs with `dry_run=true`; nothing is downloaded or moved. Cookies land in
`.probe/cookies/<apple-id>/` — delete that folder afterwards if the machine is shared.
