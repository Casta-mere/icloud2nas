# Releasing

A release is a `vX.Y.Z` git tag on a commit of `main` whose version fields all say `X.Y.Z`. CI
(`.github/workflows/ci.yml`) builds it, publishes the image `ghcr.io/casta-mere/icloud2nas` and
creates a GitHub Release. The release workflow (`.github/workflows/release.yml`) makes the version commit and the tag, so a
release is a choice made in the pull request being merged.

## Releasing from a pull request

1. **Review the pull request, then comment `/approve`.** The comment's first line must be exactly
   `/approve`, and only the repository owner's comment counts. A 👍 on the comment acknowledges
   it, and a bot comment offers the real numbers, for example from 0.1.2:
   - Major **1.0.0**
   - Minor **0.2.0**
   - Patch **0.1.3**
   - No release

   Nothing is ticked to start with. Commenting `/approve` again refreshes the numbers and keeps a
   ticked release.
2. **Tick the release you want**: one of Major, Minor and Patch. Nothing ticked, or only No
   release, merges without a release. If No release is ticked too, the ticked release wins.
3. **Merge.** The workflow's `release` job then:
   - checks out `main` (never the pull request's code) and runs `make bump PART=<part>`
   - commits `Release X.Y.Z (#N)` (`N` is the pull request) as `github-actions[bot]` and tags it
     `vX.Y.Z`
   - pushes the commit and the tag together (`git push --atomic`) with `RELEASE_TOKEN`

   Then its `notify` job comments on the pull request with the version and a link to the tag's
   CI runs.

   The number is worked out from `main` at that moment, and releases run one at a time, so two
   pull requests merged close together get consecutive versions.
4. **Watch the tag's CI run**, as in [The tag's CI run](#the-tags-ci-run): it publishes the image
   and creates the [GitHub Release](#the-github-release). Then
   [update your hosts](deploy.md#updating).

When the merge doesn't release:

| At merge | What happens |
| --- | --- |
| No choice comment (nobody commented `/approve`), or none of Major, Minor and Patch ticked | Nothing. The run says so and passes. |
| More than one of Major, Minor and Patch ticked | Nothing is released. A comment on the pull request says why and the run fails. Release with a [manual run](#releasing-without-a-pull-request). |
| Merged by anyone but the owner | Nothing. The jobs are skipped. |
| `RELEASE_TOKEN` missing or expired | The release job fails before changing anything. Fix the secret ([setup](#one-time-setup-release_token)), then do a [manual run](#releasing-without-a-pull-request). |
| `main` moved while the release job ran | The atomic push is rejected and nothing is pushed. Do a [manual run](#releasing-without-a-pull-request). |
| The workflow is re-run after its release went out | Nothing: it finds the pull request's `Release X.Y.Z (#N)` commit on `main` and stops. A failed `notify` (the pull request comments) can be re-run safely. |

GitHub keeps at most one release run waiting behind the running one. If a third release starts
while one runs and one waits, the waiting run is cancelled; do a manual run for it.

## Releasing without a pull request

Actions → release → Run workflow, and pick `patch`, `minor` or `major`. The same release job runs
from the tip of `main`. Only the owner's runs do anything.

## Releasing by hand

The fallback when the workflow can't be used:

1. **Bump the version in a pull request.** Run `make bump PART=patch` (or `minor`, `major`, or an
   exact `PART=X.Y.Z`). It writes the new version into the four hand-editable fields, regenerates
   `uv.lock` with `uv lock`, then checks that all five agree:
   - `pyproject.toml`: `project.version`
   - `uv.lock`: the `icloud2nas` package (never edit it by hand)
   - `src/icloud2nas/__init__.py`: `__version__`
   - `web/package.json`: `version`
   - `web/package-lock.json`: the top-level `version` and `packages[""].version`

   It refuses, and changes nothing, when the current version isn't a plain `X.Y.Z`.
   `tests/test_toolchain.py` fails unless the fields agree, and CI's `uv sync --locked` fails on
   a stale `uv.lock`. Merge the pull request with No release ticked.
2. **Tag the merge commit and push the tag.** The owner does this; agents never push.

       git tag vX.Y.Z <merge commit>
       git push origin vX.Y.Z

## The tag's CI run

The tag's `release-guard` job fails unless the tag is `v` plus digits only (`vX.Y.Z`) and `X.Y.Z`
equals `pyproject.toml`'s version. If it fails, nothing is built or published. Otherwise the
checks run, both platforms build, `publish` pushes the tags below, and ci.yml's `release` job
creates the GitHub Release. Then update your hosts, as in [Updating](deploy.md#updating).

### The GitHub Release

ci.yml's `release` job (not the release workflow's) runs only once `publish` has succeeded, so a
Release never points at an image that is not there. It runs

    gh release create vX.Y.Z --verify-tag --generate-notes --title vX.Y.Z

with the built-in `GITHUB_TOKEN` (`contents: write` on that job only; `RELEASE_TOKEN` is not
used). The notes open with the image tags `publish` pushed, followed by GitHub's generated notes:
the pull requests merged since the previous Release. When the Release already exists (a re-run of
the tag's CI run), the job leaves it as it is.

v0.1.0 to v0.1.3 were tagged before this job existed and have no Release. The owner may create
them by hand with the command above, oldest first so that the newest ends up as the latest
Release. Do it before the next tag if that tag's generated notes should start at v0.1.3.

## One-time setup: `RELEASE_TOKEN`

The release job pushes with a token of its own because a tag pushed with the workflow's built-in
`GITHUB_TOKEN` doesn't start other workflows, so `ci.yml` would never publish it.

1. **Create a fine-grained personal access token:** GitHub → Settings → Developer settings →
   Personal access tokens → Fine-grained tokens → Generate new token.
   - Resource owner: the owner account. Repository access: Only select repositories →
     `icloud2nas`.
   - Repository permissions: **Contents: Read and write**. Nothing else; GitHub adds Metadata
     (read-only) by itself.
   - Expiration: the longest offered (at most a year). Note the date.
2. **Store it:** the repository's Settings → Secrets and variables → Actions → New repository
   secret, named `RELEASE_TOKEN`.
3. **Renew it before it expires:** regenerate the token on its page and paste the new value into
   the secret. Until you do, the release job stops at its first step with "the RELEASE_TOKEN
   secret is missing" or "... is expired", and nothing is pushed.

If `main` ever gets branch protection, the token's account must be allowed to push to it directly.

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
