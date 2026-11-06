"""ci.yml's `github-release` job: its wiring, and its script run against a fake gh."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
IMAGE = "ghcr.io/casta-mere/icloud2nas"
TAGS = f"{IMAGE}:1.2.3\n{IMAGE}:1.2\n{IMAGE}:latest\n"
RELEASE_STEP = "Create the GitHub Release"

# What `gh api .../releases/generate-notes --jq .body` prints (gh adds the final newline).
GENERATED = (
    "## What's Changed\n"
    "* Add a thing by @alice in https://github.com/owner/repo/pull/12\n"
    "* Fix a bug by @bob in https://github.com/owner/repo/pull/13\n"
    "\n"
    "## New Contributors\n"
    "* @bob made their first contribution in https://github.com/owner/repo/pull/13\n"
    "\n"
    "**Full Changelog**: https://github.com/owner/repo/compare/v1.2.2...v1.2.3\n"
)
IMAGE_BLOCK = (
    "## Image\n"
    "\n"
    "For `linux/amd64` and `linux/arm64`:\n"
    "\n"
    f"- `{IMAGE}:1.2.3`\n"
    f"- `{IMAGE}:1.2`\n"
    f"- `{IMAGE}:latest`\n"
    "\n"
    "Update a host with `docker compose pull && docker compose up -d`. To stay on this "
    "version, set `I2N_TAG=1.2.3` in `.env`; see "
    "[docs/deploy.md](https://github.com/owner/repo/blob/v1.2.3/docs/deploy.md).\n"
)
WITH_SECTIONS = {
    "12": (
        "Adds a thing.\r\n\r\n## Release notes\r\n\r\n"
        "<!-- For whoever runs icloud2nas. -->\r\n"
        "- You can now do X.\r\n\r\n## Why\r\n\r\nBecause.\r\n"
    ),
    "13": (
        "## Release notes\n\n- Fixed Y.\n- After upgrading, restart Z.\n\n"
        "## Test plan\n\n- [x] make check\n"
    ),
}
WITHOUT_SECTIONS: dict[str, str | None] = {
    "12": None,
    "13": "Fixes a bug.\n\n## Release notes\n\n<!-- Leave empty if users won't notice. -->\n",
}

# Logs each call. `api` answers from the canned notes and bodies (null body as `.body // ""`
# prints it), and fails like gh for a path in FAKE_GH_FAIL or a pull request it doesn't know.
FAKE_GH = """\
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["release", "view"]:
    sys.exit(int(os.environ["FAKE_GH_VIEW_EXIT"]))
if args[:1] == ["api"]:
    path = args[1]
    if path == os.environ.get("FAKE_GH_FAIL"):
        sys.exit("HTTP 502: Bad Gateway (https://api.github.com/" + path + ")")
    if path.endswith("/releases/generate-notes"):
        with open(os.environ["FAKE_GH_NOTES"], encoding="utf-8") as f:
            out = f.read()
    else:
        with open(os.environ["FAKE_GH_BODIES"], encoding="utf-8") as f:
            bodies = json.load(f)
        number = path.rsplit("/", 1)[1]
        if number not in bodies:
            sys.exit("HTTP 404: Not Found (https://api.github.com/" + path + ")")
        out = (bodies[number] or "") + "\\n"
    sys.stdout.buffer.write(out.encode("utf-8"))
"""


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _release_step() -> dict[str, Any]:
    (step,) = [s for s in _jobs()["github-release"]["steps"] if s.get("name") == RELEASE_STEP]
    return dict(step)


def _run_release(
    tmp_path: Path,
    *,
    release_exists: bool,
    tags: str = TAGS,
    bodies: dict[str, str | None] | None = None,
    fail: str = "",
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    script = tmp_path / "release.sh"
    script.write_text(_release_step()["run"], encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
    gh.chmod(0o755)
    # The runner's python3; scripts/release_notes.py is stdlib only.
    python3 = bin_dir / "python3"
    python3.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n', encoding="utf-8")
    python3.chmod(0o755)
    generated = tmp_path / "generated.md"
    generated.write_text(GENERATED, encoding="utf-8")
    bodies_file = tmp_path / "bodies.json"
    bodies_file.write_text(json.dumps(WITH_SECTIONS if bodies is None else bodies), "utf-8")
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    log = tmp_path / "gh.log"
    log.touch()
    env = {
        "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
        "GH_LOG": str(log),
        "FAKE_GH_VIEW_EXIT": "0" if release_exists else "1",
        "FAKE_GH_NOTES": str(generated),
        "FAKE_GH_BODIES": str(bodies_file),
        "FAKE_GH_FAIL": fail,
        "GH_REPO": "owner/repo",
        "GITHUB_REF_NAME": "v1.2.3",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_SERVER_URL": "https://github.com",
        "RUNNER_TEMP": str(runner_temp),
        "TAGS": tags,
    }
    # The shell GitHub runs for `shell: bash`, in the checkout (the job checks the tag out).
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return done, calls


def _api_calls(*prs: int) -> list[list[str]]:
    return [
        ["api", "repos/owner/repo/releases/generate-notes", "-f", "tag_name=v1.2.3",
         "--jq", ".body"],
        *(["api", f"repos/owner/repo/pulls/{pr}", "--jq", '.body // ""'] for pr in prs),
    ]


def test_release_runs_on_version_tags_after_publish() -> None:
    release = _jobs()["github-release"]
    assert release["needs"] == "publish"
    assert release["if"] == "startsWith(github.ref, 'refs/tags/v')"


def test_only_the_github_release_job_may_write_contents() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    writers = [
        name for name, job in workflow["jobs"].items()
        if job.get("permissions", {}).get("contents") == "write"
    ]
    assert writers == ["github-release"]
    assert workflow["jobs"]["github-release"]["permissions"] == {
        "contents": "write",
        "pull-requests": "read",
    }


def test_release_checks_out_the_tag_without_credentials() -> None:
    first = _jobs()["github-release"]["steps"][0]
    assert first["uses"] == "actions/checkout@v7"
    assert first["with"] == {"persist-credentials": False}


def test_release_uses_the_built_in_token_only() -> None:
    release = _jobs()["github-release"]
    assert _release_step()["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "secrets." not in yaml.safe_dump(release)


def test_release_script_has_no_expressions() -> None:
    # Pull request bodies are untrusted: nothing is pasted into the script by the runner.
    assert "${{" not in _release_step()["run"]


def test_publish_hands_its_image_tags_to_github_release() -> None:
    jobs = _jobs()
    assert jobs["publish"]["outputs"]["tags"] == "${{ steps.meta.outputs.tags }}"
    assert _release_step()["env"]["TAGS"] == "${{ needs.publish.outputs.tags }}"


def test_release_notes_are_the_summary_the_image_tags_and_the_generated_notes(
    tmp_path: Path,
) -> None:
    done, calls = _run_release(tmp_path, release_exists=False)

    assert done.returncode == 0, done.stderr
    notes = tmp_path / "runner-temp" / "release-notes.md"
    assert calls == [
        ["release", "view", "v1.2.3", "--json", "tagName"],
        *_api_calls(12, 13),
        ["release", "create", "v1.2.3", "--verify-tag", "--title", "v1.2.3",
         "--notes-file", str(notes)],
    ]
    assert notes.read_text(encoding="utf-8") == (
        "## Summary\n"
        "\n"
        "- You can now do X.\n"
        "\n"
        "- Fixed Y.\n"
        "- After upgrading, restart Z.\n"
        "\n"
        + IMAGE_BLOCK
        + "\n"
        + GENERATED
    )


def test_without_release_notes_sections_there_is_no_summary(tmp_path: Path) -> None:
    done, calls = _run_release(tmp_path, release_exists=False, bodies=WITHOUT_SECTIONS)

    assert done.returncode == 0, done.stderr
    notes = tmp_path / "runner-temp" / "release-notes.md"
    assert calls[1:-1] == _api_calls(12, 13)
    assert calls[-1][:2] == ["release", "create"]
    assert notes.read_text(encoding="utf-8") == IMAGE_BLOCK + "\n" + GENERATED


def test_a_rerun_leaves_the_existing_release_alone(tmp_path: Path) -> None:
    done, calls = _run_release(tmp_path, release_exists=True)

    assert done.returncode == 0, done.stderr
    assert calls == [["release", "view", "v1.2.3", "--json", "tagName"]]
    assert "already exists" in done.stdout


def test_no_image_tags_fails_without_a_release(tmp_path: Path) -> None:
    done, calls = _run_release(tmp_path, release_exists=False, tags="")

    assert done.returncode == 1
    assert "no image tags" in done.stdout
    assert [call[:2] for call in calls] == [["release", "view"]]


def test_failing_generated_notes_fail_without_a_release(tmp_path: Path) -> None:
    fail = "repos/owner/repo/releases/generate-notes"
    done, calls = _run_release(tmp_path, release_exists=False, fail=fail)

    assert done.returncode != 0
    assert "HTTP 502" in done.stderr
    assert calls[1:] == _api_calls()


def test_a_failing_pull_request_read_fails_without_a_release(tmp_path: Path) -> None:
    done, calls = _run_release(tmp_path, release_exists=False, fail="repos/owner/repo/pulls/13")

    assert done.returncode != 0
    assert "HTTP 502" in done.stderr
    assert calls[1:] == _api_calls(12, 13)
