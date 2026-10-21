#!/usr/bin/env python3
"""Read and rewrite pins.toml, the one place basalt says which commit of each component it builds.

Both workflows go through this file so the manifest has one parser. Reading uses tomllib, so a
malformed manifest fails here rather than being half-read by sed. Rewriting replaces the one
`commit = "..."` line inside the named table and nothing else, so comments and layout survive; it
refuses rather than guesses when that line is not exactly where it should be.

Usage:
    helpers/pins.py get <component>            print repository=, slug= and commit= lines
    helpers/pins.py set <component> <commit>   rewrite that component's commit in place
    helpers/pins.py only-bump <base> <head>    exit 0 if base..head is a pin bump and nothing else

`get` prints in the KEY=VALUE shape GitHub Actions reads from $GITHUB_OUTPUT. `slug` is the
`owner/name` form actions/checkout wants; only github.com repositories are accepted for now.

`only-bump` is the pin-bump workflow's test before it arms auto-merge: every changed file is either
pins.toml or an existing recipe, and a recipe changed only in the `key = "sha256:..."` lines of its
`[package_digest]` table, with its other text byte-identical and its parsed tables equal. It prints
one line per refusal and exits 1, so a reason can go straight into the pull request.

Name and interface provisional.
"""

import pathlib
import re
import subprocess
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


DIGEST_LINE = re.compile(r'[A-Za-z0-9_]+ = "sha256:[0-9a-f]{64}"')
TABLE_HEADER = re.compile(r"\s*\[([^\]]+)\]\s*")


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def tables(text):
    """Split a recipe's text into [header, lines] pairs; header is None before the first table."""
    out = [[None, []]]
    for line in text.splitlines():
        header = TABLE_HEADER.fullmatch(line)
        if header:
            out.append([header.group(1).strip(), []])
        else:
            out[-1][1].append(line)
    return out


def non_digest(lines):
    return [line for line in lines if not DIGEST_LINE.fullmatch(line)]


def recipe_refusals(path, old_text, new_text):
    old, new = tables(old_text), tables(new_text)
    if [h for h, _ in old] != [h for h, _ in new]:
        return [f"{path}: its tables were added, removed or reordered"]
    refusals = []
    for (header, a), (_, b) in zip(old, new):
        if header != "package_digest":
            if a != b:
                where = header or "top"
                refusals.append(f"{path}: changed outside [package_digest] (in [{where}])")
        elif non_digest(a) != non_digest(b):
            refusals.append(f"{path}: [package_digest] changed a line that is not a digest")
    # The text test above reads table headers by line; this one reads TOML, so a header hidden in a
    # multi-line string cannot pass the first and fool the second.
    try:
        old_data, new_data = tomllib.loads(old_text), tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as error:
        return refusals + [f"{path}: not valid TOML ({error})"]
    old_data.pop("package_digest", None)
    new_data.pop("package_digest", None)
    if old_data != new_data:
        refusals.append(f"{path}: its parsed tables differ outside [package_digest]")
    return refusals


def only_bump(base, head):
    refusals = []
    for row in git("diff", "--name-status", "--no-renames", base, head).splitlines():
        status, path = row.split("\t", 1)
        if status == "M" and path == MANIFEST.name:
            continue
        if status != "M" or not re.fullmatch(r"recipes/[^/]+\.toml", path):
            refusals.append(f"{path}: {status}, and only pins.toml and existing recipes may change")
            continue
        old, new = git("show", f"{base}:{path}"), git("show", f"{head}:{path}")
        refusals += recipe_refusals(path, old, new)
    for refusal in refusals:
        print(refusal)
    sys.exit(1 if refusals else 0)


def main(argv):
    if len(argv) == 2 and argv[0] == "get":
        get(argv[1])
    elif len(argv) == 3 and argv[0] == "set":
        set_commit(argv[1], argv[2])
    elif len(argv) == 3 and argv[0] == "only-bump":
        only_bump(argv[1], argv[2])
    else:
        fail(
            "usage: helpers/pins.py get <component> | set <component> <commit>"
            " | only-bump <base> <head>"
        )


if __name__ == "__main__":
    main(sys.argv[1:])
