"""Pick the released pull requests' `## Release notes` sections for the GitHub Release page.

    python scripts/release_notes.py prs --server-url URL --repo OWNER/REPO
                                        GitHub's generated release notes on stdin; print the
                                        numbers of REPO's pull requests they link, one per
                                        line, first seen first, each once
    python scripts/release_notes.py section
                                        a pull request body on stdin; print its cleaned
                                        `## Release notes` section, or nothing

A section starts at a `## Release notes` heading (any case) and runs to the next `#` or `##`
heading or the end of the body; `###` and deeper headings stay in it. HTML comments are dropped
and the blank lines around what is left are trimmed; the rest is Markdown, used verbatim. Pull
request bodies are untrusted: they only ever pass through stdin and stdout.
Stdlib only: ci.yml's github-release job runs it on the runner's python3.
"""

from __future__ import annotations

import argparse
import re
import sys

# An unclosed comment hides the rest of the body on GitHub, so it is dropped to the end too.
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
_HEADING = re.compile(r"##[ \t]+release[ \t]+notes[ \t]*", re.IGNORECASE)
_SECTION_END = re.compile(r"#{1,2}(?:[ \t]|$)")


def pull_requests(notes: str, server_url: str, repo: str) -> list[int]:
    """The numbers of `repo`'s pull requests linked from `notes`, first seen first, each once.

    Only `<server_url>/<repo>/pull/<N>` links count, in any case as on GitHub: so the "New
    Contributors" lines repeat a number already seen, and the "Full Changelog" compare link and
    other repositories' links are ignored.
    """
    prefix = re.escape(f"{server_url.rstrip('/')}/{repo}/pull/")
    numbers: dict[int, None] = {}
    for m in re.finditer(rf"{prefix}([0-9]+)\b", notes, re.IGNORECASE):
        numbers.setdefault(int(m[1]))
    return list(numbers)


def section(body: str) -> str:
    """The cleaned `## Release notes` section of a pull request body; "" when there is none."""
    text = _COMMENT.sub("", body.replace("\r\n", "\n").replace("\r", "\n"))
    lines = text.split("\n")
    start = next((i for i, line in enumerate(lines) if _HEADING.fullmatch(line)), None)
    if start is None:
        return ""
    end = next(
        (i for i in range(start + 1, len(lines)) if _SECTION_END.match(lines[i])), len(lines)
    )
    kept = lines[start + 1 : end]
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_prs = sub.add_parser("prs", help="print the pull requests the generated notes link")
    p_prs.add_argument("--server-url", required=True, metavar="URL")
    p_prs.add_argument("--repo", required=True, metavar="OWNER/REPO")
    sub.add_parser("section", help="print the Release notes section of the body on stdin")
    args = parser.parse_args(argv)

    # UTF-8 both ways whatever the locale: bodies are often not ASCII.
    text = sys.stdin.buffer.read().decode("utf-8")
    if args.command == "prs":
        out = "".join(f"{n}\n" for n in pull_requests(text, args.server_url, args.repo))
    else:
        found = section(text)
        out = f"{found}\n" if found else ""
    sys.stdout.buffer.write(out.encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
