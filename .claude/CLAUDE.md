# icloud2nas — AI Knowledge Base

All AI knowledge files are centralized in [`.agent/`](../.agent/) at the project root.

## Ground rules

- Development runs inside the `dev` compose service: `make test`, `make lint`,
  `make typecheck` (all wrap `docker compose run --rm dev uv run ...`). Do not
  install Python or uv on the host.
- `src/icloud2nas/engine/icloudpd_adapter.py` is the only module allowed to call
  icloudpd/pyicloud internals (plus the filename helpers used by `engine/paths.py`,
  `StatusExchange` in `engine/mfa.py`, exception classes in `engine/errors.py`).
  icloudpd is pinned; bump it only together with `check_touchpoints()` and a probe run.
- Library files are never unlinked. Deletes go through `engine/trash.py`.
- Never `git push` from an agent session; commit only after the owner confirms.
- The orchestrator talks to the worker only through docs/worker-protocol.md; never import worker/adapter modules from icloud2nas.orchestrator.
