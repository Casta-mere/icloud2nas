# Releasing

A release is a `vX.Y.Z` git tag on a commit of `main` whose version fields all say `X.Y.Z`. CI
(`.github/workflows/ci.yml`) builds it and publishes the image `ghcr.io/casta-mere/icloud2nas`.

## Steps

1. **Bump the version in a pull request.** All five fields must say the new `X.Y.Z`:
   - `pyproject.toml`: `project.version`
   - `uv.lock`: the `icloud2nas` package. Regenerate it with
     `docker compose run --rm dev uv lock`; don't edit it by hand.
   - `src/icloud2nas/__init__.py`: `__version__`
   - `web/package.json`: `version`
   - `web/package-lock.json`: the top-level `version` and `packages[""].version`

   `tests/test_toolchain.py` fails unless they all agree, and CI's `uv sync --locked` fails on a
   stale `uv.lock`.
2. **Merge, then tag the merge commit and push the tag.** The owner does this; agents never push.

       git tag vX.Y.Z <merge commit>
       git push origin vX.Y.Z

3. **Watch the tag's CI run.** Its `release-guard` job fails unless the tag is `v` plus digits
   only (`vX.Y.Z`) and `X.Y.Z` equals `pyproject.toml`'s version. If it fails, nothing is built
   or published. Otherwise the checks run, both platforms build, and `publish` pushes the tags
   below.
4. **Update your hosts**, as in [Updating](deploy.md#updating).

## Image tags

| Event | Builds | Tags pushed |
| --- | --- | --- |
| Pull request | `linux/amd64`, `linux/arm64` | none |
| Push to `main` | `linux/amd64`, `linux/arm64` | `main`, `sha-<7 chars>` |
| Tag `vX.Y.Z` | `linux/amd64`, `linux/arm64` | `X.Y.Z`, `X.Y`, `latest` |

- `latest` moves only on a release tag, so a host that follows it gets releases only.
- Other tags, including pre-releases such as `v0.2.0-rc.1`, trigger nothing. There is no `X`
  (major-only) tag.
- Nothing is published from a run whose checks failed.
- Each image's `org.opencontainers.image.version` label is the release version (`X.Y.Z`), or
  `main` for builds of `main`.
