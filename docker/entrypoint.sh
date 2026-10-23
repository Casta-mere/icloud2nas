#!/bin/sh
# Root by default (per-account owners need it). With both PUID and PGID set, drop to that
# user for single-user mode; the orchestrator then reports privileged:false.
set -eu
if [ -n "${PUID:-}" ] && [ -n "${PGID:-}" ]; then
    exec setpriv --reuid "$PUID" --regid "$PGID" --clear-groups icloud2nas-serve "$@"
fi
if [ -n "${PUID:-}" ] || [ -n "${PGID:-}" ]; then
    echo "icloud2nas: set both PUID and PGID for single-user mode, or neither to run as root" >&2
    exit 1
fi
exec icloud2nas-serve "$@"
