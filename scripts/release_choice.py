"""Render and parse the release choice comment the release workflow keeps on a pull request.

    python scripts/release_choice.py render --current X.Y.Z [--selected CHOICE]
    python scripts/release_choice.py render-released --version X.Y.Z --part PART
    python scripts/release_choice.py released-note --version X.Y.Z --runs-url URL
    python scripts/release_choice.py parse        body on stdin; print the ticked choice (exit 2
                                                  when more than one box is ticked)
    python scripts/release_choice.py is-approve   body on stdin; exit 0 if it is /approve, else 1
    python scripts/release_choice.py find         `gh api --paginate .../comments` on stdin; print
                                                  the id of the bot's choice comment, if any

Options are read by their hidden `<!-- major|minor|patch|none -->` token, never by their wording.
Stdlib only: the release workflow runs it on the runner's python3.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, Literal, cast, get_args

from bump_version import PARTS, Part, next_version

MARKER = "<!-- icloud2nas-release-choice -->"
BOT = "github-actions[bot]"
Choice = Literal["major", "minor", "patch", "none"]
CHOICES: tuple[str, ...] = get_args(Choice)

_OPTION = re.compile(r"^\s*[-*] \[( |x|X)\] .*<!-- (major|minor|patch|none) -->\s*$")


def is_approve_command(body: str) -> bool:
    """True when the first line, trimmed, is exactly `/approve`."""
    lines = body.splitlines()
    return bool(lines) and lines[0].strip() == "/approve"


def render(current: str, selected: Choice = "none") -> str:
    """The choice comment for a repository at version `current`, with `selected` ticked."""
    if selected not in CHOICES:
        raise ValueError(f"unknown choice {selected!r}; expected one of {', '.join(CHOICES)}")
    labels = {p: f"{p.capitalize()}: **{next_version(current, cast(Part, p))}**" for p in PARTS}
    labels["none"] = "No release"
    lines = [
        MARKER,
        f"**Release after merge?** Move the tick to one option (two ticks release nothing). "
        f"Current version: `{current}`; the number is worked out again at merge.",
        "",
    ]
    for choice in CHOICES:
        tick = "x" if choice == selected else " "
        lines.append(f"- [{tick}] {labels[choice]} <!-- {choice} -->")
    return "\n".join(lines)


def render_released(version: str, part: Part) -> str:
    """The choice comment once its pull request has been released: nothing left to tick."""
    return f"{MARKER}\n**Released `{version}`** ({part}) when this pull request was merged."


def parse(body: str) -> Choice:
    """The ticked option; `none` when nothing is ticked. Two or more ticks raise ValueError."""
    ticked = []
    for line in body.splitlines():
        m = _OPTION.match(line)
        if m is not None and m[1] != " ":
            ticked.append(m[2])
    if len(ticked) > 1:
        raise ValueError("tick exactly one option")
    return cast(Choice, ticked[0]) if ticked else "none"


def find_comment(comments: Iterable[Mapping[str, Any]]) -> int | None:
    """The id of the first comment that the bot wrote and that carries MARKER."""
    for comment in comments:
        user = comment.get("user") or {}
        if user.get("login") == BOT and MARKER in (comment.get("body") or ""):
            return int(comment["id"])
    return None


def _comments(text: str) -> Iterator[Mapping[str, Any]]:
    """Comments from `gh api --paginate`, which prints each page's JSON array back to back."""
    decoder = json.JSONDecoder()
    i = 0
    while True:
        while i < len(text) and text[i].isspace():
            i += 1
        if i == len(text):
            return
        value, i = decoder.raw_decode(text, i)
        yield from value if isinstance(value, list) else [value]


def released_note(version: str, runs_url: str) -> str:
    return (
        f"Released **{version}**: `Release {version}` is on `main`, tagged `v{version}`. "
        f"CI builds and publishes the image: [runs for v{version}]({runs_url})."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_render = sub.add_parser("render", help="print the choice comment")
    p_render.add_argument("--current", required=True, metavar="X.Y.Z")
    p_render.add_argument("--selected", choices=CHOICES, default="none")
    p_released = sub.add_parser("render-released", help="print the comment after a release")
    p_released.add_argument("--version", required=True, metavar="X.Y.Z")
    p_released.add_argument("--part", required=True, choices=PARTS)
    p_note = sub.add_parser("released-note", help="print the note posted after a release")
    p_note.add_argument("--version", required=True, metavar="X.Y.Z")
    p_note.add_argument("--runs-url", required=True)
    sub.add_parser("parse", help="print the ticked choice of the comment on stdin")
    sub.add_parser("is-approve", help="exit 0 if the comment on stdin is /approve")
    sub.add_parser("find", help="print the id of the choice comment among those on stdin")
    args = parser.parse_args(argv)

    try:
        if args.command == "render":
            print(render(args.current, args.selected))
        elif args.command == "render-released":
            print(render_released(args.version, args.part))
        elif args.command == "released-note":
            print(released_note(args.version, args.runs_url))
        elif args.command == "parse":
            try:
                print(parse(sys.stdin.read()))
            except ValueError as e:
                print(e, file=sys.stderr)
                return 2
        elif args.command == "find":
            found = find_comment(_comments(sys.stdin.read()))
            if found is not None:
                print(found)
        else:
            return 0 if is_approve_command(sys.stdin.read()) else 1
    except ValueError as e:
        print(f"release_choice: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
