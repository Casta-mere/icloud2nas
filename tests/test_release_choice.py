import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_choice.py"
sys.path.insert(0, str(ROOT / "scripts"))

from release_choice import (  # noqa: E402
    MARKER,
    Choice,
    already_released,
    find_comment,
    is_approve_command,
    parse,
    release_subject,
    released_note,
    render,
    render_released,
)


def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )


def test_render_shows_real_numbers_and_defaults_to_none() -> None:
    body = render("0.1.2")
    assert body.startswith(MARKER)
    assert "**1.0.0**" in body and "**0.2.0**" in body and "**0.1.3**" in body
    assert "`0.1.2`" in body
    assert parse(body) == "none"
    # nothing is pre-ticked: the owner ticks the one release they want
    assert not any("[x]" in line for line in body.splitlines())


@pytest.mark.parametrize("choice", ["major", "minor", "patch"])
def test_render_keeps_a_selection(choice: Choice) -> None:
    body = render("0.1.2", choice)
    assert parse(body) == choice
    assert sum("[x]" in line for line in body.splitlines()) == 1


def test_render_never_ticks_no_release() -> None:
    # a refresh of a comment with No release ticked shows it unticked; both mean no release
    body = render("0.1.2", "none")
    assert parse(body) == "none"
    assert not any("[x]" in line for line in body.splitlines())


def test_parse_by_token_not_wording() -> None:
    assert parse("- [X] anything <!-- patch -->") == "patch"
    assert parse("* [x] Minor, reworded by hand <!-- minor -->  ") == "minor"
    # The wording names a part, but the token decides.
    assert parse("- [x] Major: **1.0.0** <!-- none -->") == "none"
    # A tick without a token is not an option.
    assert parse("- [x] Patch: **0.1.3**") == "none"


def test_parse_a_ticked_release_wins_over_none() -> None:
    assert parse("- [x] No release <!-- none -->\n- [x] Patch <!-- patch -->") == "patch"
    # The owner only ticks Patch; No release stays ticked from the start.
    assert parse(render("0.1.2").replace("- [ ] Patch", "- [X] Patch")) == "patch"


def test_parse_two_release_ticks_is_an_error() -> None:
    with pytest.raises(ValueError, match="tick exactly one release option"):
        parse("- [x] Minor <!-- minor -->\n- [x] Patch <!-- patch -->")
    with pytest.raises(ValueError, match="tick exactly one release option"):
        parse(render("0.1.2", "major").replace("- [ ] Minor", "- [X] Minor"))
    # No release ticked as well doesn't help.
    all_but_major = render("0.1.2").replace("[ ]", "[x]").replace("[x] Major", "[ ] Major")
    with pytest.raises(ValueError, match="tick exactly one release option"):
        parse(all_but_major)


def test_parse_no_release_ticked_means_none() -> None:
    assert parse("- [x] No release <!-- none -->") == "none"
    assert parse(render("0.1.2").replace("[x]", "[ ]")) == "none"
    assert parse("") == "none"


def test_parse_reads_crlf_bodies() -> None:
    assert parse(render("0.1.2", "patch").replace("\n", "\r\n")) == "patch"


def test_is_approve_command() -> None:
    assert is_approve_command("/approve")
    assert is_approve_command("  /approve  \nlooks good")
    assert is_approve_command("/approve\r\nthanks")
    assert not is_approve_command("lgtm /approve")
    assert not is_approve_command("/approved")
    assert not is_approve_command("looks good\n/approve")
    assert not is_approve_command("")


def test_render_released_has_nothing_left_to_tick() -> None:
    body = render_released("0.1.3", "patch")
    assert body.startswith(MARKER) and "0.1.3" in body
    assert "[ ]" not in body and "[x]" not in body
    assert parse(body) == "none"


def test_released_note_names_the_version_tag_and_runs() -> None:
    url = "https://github.com/o/r/actions/workflows/ci.yml?query=branch%3Av0.1.3"
    note = released_note("0.1.3", url)
    assert "0.1.3" in note and "`v0.1.3`" in note and url in note


def test_cli_render_and_parse_round_trip() -> None:
    rendered = run("render", "--current", "0.1.2", "--selected", "minor")
    assert rendered.returncode == 0, rendered.stderr
    assert rendered.stdout == render("0.1.2", "minor") + "\n"
    parsed = run("parse", stdin=rendered.stdout)
    assert (parsed.returncode, parsed.stdout) == (0, "minor\n")


def test_cli_parse_two_release_ticks_exits_2() -> None:
    result = run("parse", stdin="- [x] a <!-- major -->\n- [x] b <!-- patch -->\n")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "tick exactly one release option" in result.stderr
    with_none = run("parse", stdin="- [x] a <!-- none -->\n- [x] b <!-- patch -->\n")
    assert (with_none.returncode, with_none.stdout) == (0, "patch\n")


def test_cli_is_approve() -> None:
    assert run("is-approve", stdin="/approve\n").returncode == 0
    assert run("is-approve", stdin="lgtm /approve\n").returncode == 1


def test_cli_released_note() -> None:
    result = run("released-note", "--version", "0.1.3", "--runs-url", "https://x/runs")
    assert result.returncode == 0, result.stderr
    assert result.stdout == released_note("0.1.3", "https://x/runs") + "\n"


COMMENTS = [
    {"id": 1, "user": {"login": "Casta-mere"}, "body": f"quoting {MARKER} by hand"},
    {"id": 2, "user": {"login": "github-actions[bot]"}, "body": "some other bot comment"},
    {"id": 3, "user": None, "body": MARKER},
    {"id": 4, "user": {"login": "github-actions[bot]"}, "body": render("0.1.2")},
    {"id": 5, "user": {"login": "github-actions[bot]"}, "body": render("0.1.2")},
]


def test_find_comment_wants_the_bots_marker_comment() -> None:
    assert find_comment(COMMENTS) == 4
    assert find_comment(COMMENTS[:3]) is None
    assert find_comment([]) is None


def test_cli_find_reads_paginated_output() -> None:
    # `gh api --paginate` prints one JSON array per page, back to back.
    pages = json.dumps(COMMENTS[:2]) + json.dumps(COMMENTS[2:], indent=2) + "\n"
    result = run("find", stdin=pages)
    assert (result.returncode, result.stdout) == (0, "4\n")
    nothing = run("find", stdin=json.dumps(COMMENTS[:3]) + "[]")
    assert (nothing.returncode, nothing.stdout) == (0, "")
    assert run("find", stdin="").stdout == ""
    assert run("find", stdin="not json").returncode == 1


def test_cli_render_released() -> None:
    result = run("render-released", "--version", "0.1.3", "--part", "patch")
    assert result.returncode == 0, result.stderr
    assert result.stdout == render_released("0.1.3", "patch") + "\n"


def test_cli_render_refuses_a_non_release_current() -> None:
    result = run("render", "--current", "0.2.0-rc.1")
    assert result.returncode != 0 and "0.2.0-rc.1" in result.stderr


def test_release_subject_names_the_pr_only_for_pr_releases() -> None:
    assert release_subject("0.1.2", 13) == "Release 0.1.2 (#13)"
    assert release_subject("0.1.2") == "Release 0.1.2"


HISTORY = [
    "Fix the wizard (#14)",
    "Release 0.1.3",  # a manual run
    "Release 0.1.2 (#130)",
    "Release 0.1.1 (#1)",
    "Add release notes (#13)",  # the PR's own squash merge
    "Update check: show the version and announce new releases (#8)",
]


def test_already_released_matches_this_prs_release_commit() -> None:
    subjects = [*HISTORY[:3], "Release 0.1.2 (#13)", *HISTORY[3:]]
    assert already_released(subjects, 13) == "Release 0.1.2 (#13)"
    assert already_released([release_subject("2.0.0", 7)], 7) == "Release 2.0.0 (#7)"


def test_already_released_no_match() -> None:
    assert already_released(HISTORY, 13) is None  # other PRs' numbers, a manual run, a merge
    assert already_released([], 13) is None
    assert already_released([" Release 0.1.2 (#13)", "Release 0.1.2 (#13) again"], 13) is None
    assert already_released(["Release 0.1.2-rc.1 (#13)"], 13) is None


def test_already_released_ignores_a_manual_run_subject() -> None:
    assert already_released([release_subject("0.1.2")], 13) is None


def test_cli_already_released_reads_git_log() -> None:
    log = "\n".join([*HISTORY, "Release 0.1.2 (#13)"]) + "\n"
    found = run("already-released", "--pr", "13", stdin=log)
    assert (found.returncode, found.stdout) == (0, "Release 0.1.2 (#13)\n")
    assert run("already-released", "--pr", "14", stdin=log).returncode == 1
    assert run("already-released", "--pr", "13", stdin="".join(HISTORY)).returncode == 1
    assert run("already-released", "--pr", "x", stdin=log).returncode == 2


def test_cli_release_subject() -> None:
    assert run("release-subject", "--version", "0.1.2", "--pr", "13").stdout == (
        "Release 0.1.2 (#13)\n"
    )
    assert run("release-subject", "--version", "0.1.2").stdout == "Release 0.1.2\n"
