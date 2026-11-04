# Deploying icloud2nas

## Quick start

    cp compose.example.yaml compose.yaml
    cp .env.example .env        # set PHOTOS_ROOT (see below) and TZ
    docker compose up -d        # pulls ghcr.io/casta-mere/icloud2nas:latest

Open `http://<nas>:4871`, choose LAN or WAN mode (next section), add an Apple ID, then add its
libraries. State (database, cookies, logs) lives in `./data` (`/data` in the container); photos
are written under `/photos`.

The image is public and built for `linux/amd64` and `linux/arm64`. To build it yourself instead,
run `make image` in a source checkout and set `image: icloud2nas:local` in `compose.yaml`.

## Updating

    docker compose pull && docker compose up -d

`up -d` recreates the container only when the pulled image differs from the running one.
Restarting interrupts a sync that is running: the run is marked failed ("daemon restarted") and
the next scheduled run picks the library up again. Update between syncs if you can.

The service checks ghcr.io for a newer release about a minute after it starts and then once a
day (never during first-run setup, before LAN or WAN is chosen), and the web UI announces one;
it never updates itself. The check goes through the container's `HTTPS_PROXY`/`NO_PROXY` when
they are set. A failed check only shows in Settings → About. To turn the automatic checks off,
switch off "Auto-check" ("自动检查更新" in the Chinese UI) in Settings → About, or set
`I2N_UPDATE_CHECK=0` on the container before its first start (it applies only while the setting
has never been saved). The "Check for updates" button works either way.

## Pinning a version / rolling back

`compose.yaml` uses `ghcr.io/casta-mere/icloud2nas:${I2N_TAG:-latest}`, and `:latest` moves only
when a release is published. To stay on one version, or to go back to an earlier one, set it in
`.env` next to `compose.yaml`:

    I2N_TAG=0.1.1

then run `docker compose pull && docker compose up -d`. Delete the line and do the same to follow
`:latest` again. The tags are listed in [release.md](release.md#image-tags). The database is only
ever migrated forward: an older version refuses to start on a database a newer one has upgraded,
so back up `./data` before an update you might want to roll back.

## First visit: LAN or WAN

A new install answers every other API call with `428 setup required` (the session and setup
endpoints stay open) and the web UI shows only a picker, until someone chooses:

- **LAN**: no password. Anyone who can reach the port gets in, so use it only when that is just
  your home network. Choosing it from a page that came through a reverse proxy or a public
  address shows a warning; it never blocks.
- **WAN**: you set an admin password and every request, from the LAN too, must log in. Choose
  this for anything reachable from the internet.

The mode can be switched later in Settings (setting a password makes it WAN, clearing it makes
it LAN). An install that already had a password stays WAN after an upgrade; one without a
password shows the picker. If that password-less install is already reachable from outside (a
reverse proxy or a forwarded port), set a password in Settings *before* upgrading: it then comes
back in WAN mode and never shows the first-run picker on the public address. Syncs keep running
in every mode, the picker included.

Known limits:

- **First-run race.** Whoever opens the picker first chooses. Finish setup before the port or
  domain is exposed to anyone else.
- **DNS rebinding.** A malicious site visited by someone on your LAN can drive a LAN-mode install
  (or complete first-run setup) through their browser. The `application/json` requirement does
  not stop it and there is no Host-header allowlist. Use WAN mode if that matters.
- **No login throttling** in WAN mode; the password hashing cost is the only brake. Pick a
  long password.

## What to mount as `/photos`

Mount one broad directory once and let each library's folder be a path under it:

| NAS | `PHOTOS_ROOT` |
| --- | --- |
| UGREEN UGOS | `/home` |
| Synology DSM | `/volume1/homes` |
| QNAP | `/share/homes` |
| Unraid | `/mnt/user` |

Mounting broadly matters. A library folder is only ever created below `/photos`; if you mounted a
narrow folder and later changed or lost the mount, the library would look empty and be
downloaded again from scratch. icloud2nas guards against that: a library that has finished a full
sync whose folder is gone is parked as `dir_missing` and never synced (manual runs get a 409)
until the folder is back. With a single broad mount, that situation means something is really
wrong rather than "the path changed".

## File owners

- **Per-account owners (default).** The container runs as root. Each account has an owner
  (a NAS user); its sync worker runs as that uid/gid, so files land owned by that user. The
  default owner is the owner of the nearest existing parent of the library folder. The list of
  choosable users is read from `/host/etc/passwd` and `/host/etc/group` (uid 1000-59999 with a
  home under `/home`).
- **Single-user mode.** Set both `PUID` and `PGID`. The entrypoint drops to that user with
  `setpriv` before starting; the owner select is hidden and `/api/host-users` reports
  `privileged: false`. The `/data` directory must be writable by that user. Setting only one of
  the two stops the container with an error.
- **The owner is fixed once the account is added.** It cannot be changed later (the API answers
  409); to use another owner, delete the account and add it again. Accounts added before owners
  existed show "root (unset)".
- `GET /api/host-users?path=<folder under /photos>` returns `{privileged, users, suggested}`:
  the choosable users, and the owner of the nearest existing parent of that folder (`null` when
  `path` is omitted or nothing exists). A `path` that is absolute or leaves `/photos` is a 400.

## `/etc:/host/etc:ro`

Mount the whole `/etc` folder read-only, not single files such as `/etc/passwd`. Editors and
account tools replace those files with a new inode, and a single-file bind mount keeps pointing
at the old one, so the user list would silently go stale. The folder mount always sees the
current files. It is only needed in per-account owner mode.

## First login

An Apple ID signing in from a new host (this container, cookies empty) triggers a real 2FA
code on the account's trusted devices. Have the phone at hand and enter the code in the web UI.
Cookies are kept in `/data`, so later syncs reuse the session.

## Reverse proxy

- Choose WAN mode (an admin password) before exposing the port or domain. LAN mode is open to
  everyone who can reach it, and a fresh install lets the first visitor choose, so finish the
  first-run setup before the proxy goes live.
- Server-sent events live at `/api/events`; turn off buffering for them, for example in nginx
  `proxy_buffering off;` (and a generous `proxy_read_timeout`).
- Pass the usual `Host` and `X-Forwarded-*` headers.

## Back up the secret key

Apple passwords are stored encrypted with a key: `I2N_SECRET_KEY` when set, otherwise
`/data/secret.key`, generated on first start (mode 0600). Back it up together with
`/data/icloud2nas.db`. If the key is lost or changes, the stored passwords cannot be decrypted
and every Apple password must be entered again (and each account verified with a new 2FA code).
Run logs (`/data/logs`) and the database are made readable by root only at startup.

## Proxies and secrets

`HTTPS_PROXY`, `HTTP_PROXY`, `NO_PROXY` (and lower-case forms) set on the container are passed to
the sync workers. Workers run as the account's NAS user, so that user can read them from the
process environment. Do not put credentials you care about in proxy URLs.

## Trash and kept files

Inside each library folder: `<library>/.icloud2nas/trash/<YYYY-MM-DD>/...` holds trashed files
until retention expires, and `<library>/.icloud2nas/kept/...` holds files you chose to keep.
Deleted libraries are moved to `<parent>/.icloud2nas-deleted/<name>-<YYYY-MM-DD>`.
