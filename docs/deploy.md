# Deploying icloud2nas

## Quick start

    cp compose.example.yaml compose.yaml
    cp .env.example .env        # set PHOTOS_ROOT (see below) and TZ
    make image                  # builds icloud2nas:local
    docker compose up -d

Open `http://<nas>:4871`, choose LAN or WAN mode (next section), add an Apple ID, then add its
libraries. State (database, cookies, logs) lives in `./data` (`/data` in the container); photos
are written under `/photos`.

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
