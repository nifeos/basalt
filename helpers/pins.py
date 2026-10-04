#!/usr/bin/env python3
"""Read and rewrite pins.toml, the one place basalt says which commit of each component it builds.

Both workflows go through this file so the manifest has one parser. Reading uses tomllib, so a
malformed manifest fails here rather than being half-read by sed. Rewriting replaces the one
`commit = "..."` line inside the named table and nothing else, so comments and layout survive; it
refuses rather than guesses when that line is not exactly where it should be.

Usage:
    helpers/pins.py get <component>            print repository=, slug= and commit= lines
    helpers/pins.py set <component> <commit>   rewrite that component's commit in place

`get` prints in the KEY=VALUE shape GitHub Actions reads from $GITHUB_OUTPUT. `slug` is the
`owner/name` form actions/checkout wants; only github.com repositories are accepted for now.

Name and interface provisional.
"""

import pathlib
import re
import sys
import tomllib

MANIFEST = pathlib.Path(__file__).resolve().parent.parent / "pins.toml"
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
GITHUB = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?")


def fail(message):
    print(f"pins: {message}", file=sys.stderr)
    sys.exit(1)


def component(name):
    with MANIFEST.open("rb") as f:
        pins = tomllib.load(f)
    if name not in pins:
        fail(f"{MANIFEST.name} has no [{name}] table (it has: {', '.join(pins) or 'nothing'})")
    entry = pins[name]
    for key in ("repository", "commit"):
        if not isinstance(entry.get(key), str):
            fail(f"[{name}] needs a string `{key}`")
    if not FULL_COMMIT.fullmatch(entry["commit"]):
        fail(f"[{name}] commit {entry['commit']!r} is not a full 40-character lowercase commit id")
    match = GITHUB.fullmatch(entry["repository"])
    if not match:
        fail(f"[{name}] repository {entry['repository']!r} is not a https://github.com/ URL")
    return entry["repository"], match.group(1), entry["commit"]


def get(name):
    repository, slug, commit = component(name)
    print(f"repository={repository}")
    print(f"slug={slug}")
    print(f"commit={commit}")


def set_commit(name, new):
    if not FULL_COMMIT.fullmatch(new):
        fail(f"{new!r} is not a full 40-character lowercase commit id")
    _, _, old = component(name)
    lines = MANIFEST.read_text().splitlines(keepends=True)
    table = None
    hits = []
    for i, line in enumerate(lines):
        header = re.fullmatch(r"\s*\[([^\]]+)\]\s*", line)
        if header:
            table = header.group(1).strip()
        elif table == name and re.fullmatch(rf'\s*commit\s*=\s*"{old}"\s*', line):
            hits.append(i)
    if len(hits) != 1:
        fail(f"expected exactly one `commit = \"{old}\"` line in [{name}], found {len(hits)}")
    lines[hits[0]] = lines[hits[0]].replace(old, new)
    MANIFEST.write_text("".join(lines))
    if component(name)[2] != new:
        fail("the rewrite did not take; pins.toml is unchanged in meaning")


def main(argv):
    if len(argv) == 2 and argv[0] == "get":
        get(argv[1])
    elif len(argv) == 3 and argv[0] == "set":
        set_commit(argv[1], argv[2])
    else:
        fail("usage: helpers/pins.py get <component> | set <component> <commit>")


if __name__ == "__main__":
    main(sys.argv[1:])
