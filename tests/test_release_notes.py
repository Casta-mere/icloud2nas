import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release_notes.py"
sys.path.insert(0, str(ROOT / "scripts"))

from release_notes import pull_requests, section  # noqa: E402

SERVER = "https://github.com"
REPO = "owner/repo"
GENERATED = (
    "## What's Changed\n"
    "* Add a thing by @alice in https://github.com/owner/repo/pull/13\n"
    "* Fix a bug by @bob in https://github.com/owner/repo/pull/9\n"
    "* Bump a dependency by @carol in https://github.com/owner/repo/pull/21\n"
    "\n"
    "## New Contributors\n"
    "* @bob made their first contribution in https://github.com/owner/repo/pull/9\n"
    "* @carol made their first contribution in https://github.com/owner/repo/pull/21\n"
    "\n"
    "**Full Changelog**: https://github.com/owner/repo/compare/v1.2.2...v1.2.3"
)


def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def test_prs_in_first_seen_order_each_once() -> None:
    # the New Contributors lines link the same pull requests again
    assert pull_requests(GENERATED, SERVER, REPO) == [13, 9, 21]


def test_prs_ignore_the_compare_link() -> None:
    notes = "**Full Changelog**: https://github.com/owner/repo/compare/v1.2.2...v1.2.3"
    assert pull_requests(notes, SERVER, REPO) == []


def test_prs_ignore_other_repositories() -> None:
    notes = (
        "* Upstream fix by @x in https://github.com/other/repo/pull/7\n"
        "* Fork by @y in https://github.com/owner/repo-fork/pull/8\n"
        "* Ours by @z in https://github.com/owner/repo/pull/12\n"
        "* Elsewhere by @w in https://example.com/owner/repo/pull/6\n"
    )
    assert pull_requests(notes, SERVER, REPO) == [12]


def test_prs_match_owner_and_repo_in_any_case() -> None:
    notes = "* A by @x in https://github.com/Owner/Repo/pull/3\n"
    assert pull_requests(notes, SERVER + "/", REPO) == [3]


def test_prs_none() -> None:
    assert pull_requests("", SERVER, REPO) == []


def test_section_runs_to_the_next_level_2_heading() -> None:
    body = "Adds a thing.\n\n## Release notes\n\n- You can now do X.\n\n## Why\n\nBecause.\n"
    assert section(body) == "- You can now do X."


def test_section_heading_in_any_case_with_trailing_whitespace() -> None:
    assert section("## release NOTES  \t\n- X\n") == "- X"
    assert section("##   Release Notes\n- X\n") == "- X"


def test_section_ends_at_a_level_1_heading() -> None:
    assert section("## Release notes\n- X\n# Appendix\n- not this\n") == "- X"


def test_section_keeps_deeper_headings() -> None:
    body = "## Release notes\n### Upgrading\n- Do Y.\n#### Note\nZ\n## Test plan\n- ran it\n"
    assert section(body) == "### Upgrading\n- Do Y.\n#### Note\nZ"


def test_section_runs_to_the_end_of_the_body() -> None:
    assert section("Intro.\n\n## Release notes\n\n- X\n- Y\n\n\n") == "- X\n- Y"


def test_section_drops_html_comments() -> None:
    body = (
        "## Release notes\n"
        "\n"
        "<!--\n"
        "For whoever runs icloud2nas.\n"
        "## Not a heading\n"
        "-->\n"
        "- X <!-- inline -->\n"
        "\n"
        "## Why\n"
    )
    assert section(body) == "- X "


def test_section_with_only_a_comment_is_nothing() -> None:
    body = "## Release notes\n\n<!-- Leave empty if users won't notice. -->\n\n## Why\n\nCI.\n"
    assert section(body) == ""


def test_section_of_the_untouched_pull_request_template_is_nothing() -> None:
    template = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")
    assert "## Release notes" in template
    assert section(template) == ""
    filled = template.replace("## Why", "- You can now do X.\n\n## Why")
    assert section(filled) == "- You can now do X."


def test_section_a_commented_out_heading_does_not_count() -> None:
    assert section("<!-- ## Release notes\n- X -->\n## Why\n") == ""


def test_section_an_unclosed_comment_hides_the_rest() -> None:
    assert section("## Release notes\n- X\n<!-- draft\n- Y\n") == "- X"


def test_section_missing_heading_is_nothing() -> None:
    assert section("Adds a thing.\n\n## Why\n\nBecause.\n") == ""
    assert section("## Release notes and more\n- X\n") == ""
    assert section("### Release notes\n- X\n") == ""


def test_section_of_an_empty_body_is_nothing() -> None:
    assert section("") == ""
    assert section("## Release notes\n\n   \n") == ""


def test_section_reads_crlf_bodies() -> None:
    body = "Intro.\r\n\r\n## Release notes\r\n\r\n- X\r\n- Y\r\n\r\n## Why\r\nZ\r\n"
    assert section(body) == "- X\n- Y"


def test_cli_prs() -> None:
    result = run("prs", "--server-url", SERVER, "--repo", REPO, stdin=GENERATED)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "13\n9\n21\n"
    assert run("prs", "--server-url", SERVER, "--repo", REPO, stdin="").stdout == ""


def test_cli_section_prints_the_section_or_nothing() -> None:
    result = run("section", stdin="## Release notes\r\n\r\n- 会话到期前提醒。\r\n\r\n## Why\r\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "- 会话到期前提醒。\n"
    nothing = run("section", stdin="## Release notes\n<!-- nothing -->\n")
    assert (nothing.returncode, nothing.stdout) == (0, "")
    assert run("section", stdin="").stdout == ""


def test_cli_prs_needs_server_and_repo() -> None:
    assert run("prs", stdin=GENERATED).returncode == 2
