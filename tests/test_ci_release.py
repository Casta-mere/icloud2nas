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

FAKE_GH = """\
import json, os, sys
with open(os.environ["GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["release", "view"]:
    sys.exit(int(os.environ["FAKE_GH_VIEW_EXIT"]))
"""


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs


def _run_release(tmp_path: Path, *, release_exists: bool, tags: str = TAGS) -> tuple[
    subprocess.CompletedProcess[str], list[list[str]]
]:
    (step,) = _jobs()["github-release"]["steps"]
    script = tmp_path / "release.sh"
    script.write_text(step["run"], encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{FAKE_GH}", encoding="utf-8")
    gh.chmod(0o755)
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir()
    log = tmp_path / "gh.log"
    log.touch()
    env = {
        "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
        "GH_LOG": str(log),
        "FAKE_GH_VIEW_EXIT": "0" if release_exists else "1",
        "GITHUB_REF_NAME": "v1.2.3",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_SERVER_URL": "https://github.com",
        "RUNNER_TEMP": str(runner_temp),
        "TAGS": tags,
    }
    # The shell GitHub runs for `shell: bash`.
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
        env=env, capture_output=True, text=True, timeout=10, check=False,
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return done, calls


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
    assert workflow["jobs"]["github-release"]["permissions"] == {"contents": "write"}


def test_release_uses_the_built_in_token_only() -> None:
    release = _jobs()["github-release"]
    (step,) = release["steps"]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "secrets." not in yaml.safe_dump(release)


def test_publish_hands_its_image_tags_to_github_release() -> None:
    jobs = _jobs()
    assert jobs["publish"]["outputs"]["tags"] == "${{ steps.meta.outputs.tags }}"
    (step,) = jobs["github-release"]["steps"]
    assert step["env"]["TAGS"] == "${{ needs.publish.outputs.tags }}"


def test_release_is_created_with_generated_notes_and_the_image_tags(tmp_path: Path) -> None:
    done, calls = _run_release(tmp_path, release_exists=False)

    assert done.returncode == 0, done.stderr
    notes = tmp_path / "runner-temp" / "release-notes.md"
    assert calls == [
        ["release", "view", "v1.2.3", "--json", "tagName"],
        ["release", "create", "v1.2.3", "--verify-tag", "--generate-notes", "--title", "v1.2.3",
         "--notes-file", str(notes)],
    ]
    assert notes.read_text(encoding="utf-8") == (
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
