# Web UI (`web/`)

React 19 + Vite 7 + Tailwind v4 + TypeScript, talking only to `/api/*` (see [api.md](api.md)).
Nothing runs on the host except Docker: every command wraps `docker compose run --rm web npm …`.

## Dev loop

    make web-install   # npm ci into the web-node-modules volume (fixes its ownership first)
    make web-dev       # serve (API, :4871) + Vite (UI, http://localhost:5173)
    make web-check     # tsc --noEmit && eslint . && vitest run
    make web-test ARGS="src/i18n"   # vitest run <args>
    make web-build     # type-check and build web/dist

Open http://localhost:5173. Vite proxies `/api` to `VITE_API_PROXY` (compose sets
`http://serve:4871`; default `http://localhost:4871`). The proxy strips upstream compression and
marks responses `no-transform`, so the SSE stream `/api/events` flows through unbuffered.

## `serve` and `.probe`

The `serve` compose service reuses the `icloud2nas-dev` image and mounts `./.probe` at `/probe`
with `I2N_DATA_DIR=/probe/data`, `I2N_PHOTOS_DIR=/probe/photos`, `I2N_JITTER_S=0,0`. That is the
real account data: starting it migrates the database, so treat `make web-dev` as a live run.
`docker compose up serve` starts the API alone on http://localhost:4871. The `web-*` make
targets use `--no-deps` and never start `serve`.

## Linux hosts

The `web` container runs as `${I2N_UID:-1000}:${I2N_GID:-1000}`. On a Linux host whose user is not
1000, export `I2N_UID=$(id -u) I2N_GID=$(id -g)` before any `make web-*` so files written into
`web/` (lockfile, `dist/`) belong to you.

## If native modules fail

`web` uses `node:22-alpine`. If Tailwind v4's native binary or Vitest fails on musl (some arm64
setups), change the `web` service image in `compose.yaml` to `node:22-bookworm-slim`, remove the
`web-node-modules` volume (`docker volume rm icloud2nas_web-node-modules`) and run
`make web-install` again. This was not needed on Docker Desktop for Apple silicon.

## i18n

`src/i18n/zh.ts` defines the keys, `en.ts` must have the same set (a test enforces it). Use
`useT()` in components; the language defaults from `navigator.language` and is stored in
`localStorage["i2n.lang"]`.

Strings only go through `t()`: an ESLint rule (`no-restricted-syntax` on `JSXText` containing a
letter, in `src/**/*.tsx` except tests) fails the build on hard-coded text. For a brand or command
that is not translated, write it as a JS expression, `{"icloud2nas-serve …"}`. English copy should
read as natural English, not as a word-for-word translation of the Chinese.

## Reviewer checklist

Run `make web-dev`, open http://localhost:5173 and answer yes or no to each item, at desktop width
and again at 375px. The page to check is in brackets.

1. One label column per card (96px on desktop); info rows and form rows share it. [Libraries, Settings]
2. Hints sit under their control, never beside it. [Settings, Accounts wizard]
3. Buttons never wrap; titles truncate with an ellipsis; an action row wraps as a whole. [Overview, Libraries]
4. Lists are fixed-column grids with ellipsis (recent runs, trash tree, plan file list). [Overview, Trash, Libraries]
5. Radios and checkboxes are left-aligned with their text; multi-line controls are top-aligned with their label; password inputs are full width. [Settings, Accounts]
6. There are no toggles on Overview. [Overview]
7. At 640px and below: single column, bottom tabs, the account needing a code comes first and its code input is the first control in the page content (numeric keypad, 6 digits); the one-line status strip above it (connection · pause · language) is allowed. [Overview]
8. The log drawer is 640px wide on desktop and full screen on phones. [Overview, open a run]
9. Numbers use tabular figures; times are local; dates are `YYYY-MM-DD`. [Overview, Trash, Libraries]

Phone (375px):

- [ ] The five bottom tabs show; the top nav does not.
- [ ] A compact line under the brand shows the connection state, 全局暂停/继续 and the language switch (plus 退出登录 when an admin password is set).
- [ ] A waiting-for-code account is first, and its code input is the first control in the page content (only the status strip above it comes earlier).
- [ ] The log drawer covers the full width, with a visible close button.
