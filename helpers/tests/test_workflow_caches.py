"""No job that can write, and no job that builds a release nobody reviewed, touches a cache.

The recipe bump's `build` job runs the build scripts of upstream releases. A hosted runner gives
every step sudo, so such code can read the runner's cache token and save entries in main's cache
scope whatever the job declares. Those entries must never reach a job that holds a write
permission or `id-token`, nor anything published, so those jobs restore and save no cache at all.
Jobs that only read keep their caches: what they produce is a check result or a debugging artifact,
and every published package is built by `packages`' `build` job, which caches nothing either.

Workflows are read as text, since the gate's Python has no YAML parser; the layout is the one
every workflow here uses (two-space indent, jobs under a top-level `jobs:`).
"""

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

# A job that runs code from an upstream release no person has reviewed.
UNREVIEWED_BUILDS = {("recipe-bump.yml", "build")}

# Jobs whose output is published: they must not restore a cache even though they hold read only.
PUBLISHED_BUILDS = {("packages.yml", "build")}

CACHE_USE = re.compile(
    r"uses:\s*(actions/cache|Swatinem/rust-cache|mozilla-actions/sccache-action)\b"
    r"|^\s*cache(-[a-z-]+)?:\s*(?!false\b)\S"
    r"|SCCACHE_GHA|ACTIONS_CACHE_URL"
)
JOB = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
TOP_KEY = re.compile(r"^[A-Za-z]")


def code_lines(lines):
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


def permissions(block, indent):
    """The permission values in a `permissions:` block that starts at `indent`."""
    values = []
    for i, line in enumerate(block):
        match = re.match(rf"^ {{{indent}}}permissions:\s*(\S*)", line)
        if not match:
            continue
        if match.group(1):
            values.append(match.group(1))
            continue
        for inner in block[i + 1 :]:
            if len(inner) - len(inner.lstrip()) <= indent:
                break
            values.append(inner.split(":", 1)[1].split("#")[0].strip())
    return values


def jobs(text):
    """Yield (name, lines, privileged) for each job in a workflow's text."""
    lines = code_lines(text.splitlines())
    top = []
    body = []
    in_jobs = False
    for line in lines:
        if TOP_KEY.match(line):
            in_jobs = line.startswith("jobs:")
            if in_jobs:
                continue
        (body if in_jobs else top).append(line)
    top_write = any("write" in v for v in permissions(top, 0))
    names = [(i, JOB.match(line).group(1)) for i, line in enumerate(body) if JOB.match(line)]
    for n, (start, name) in enumerate(names):
        end = names[n + 1][0] if n + 1 < len(names) else len(body)
        block = body[start + 1 : end]
        own = permissions(block, 4)
        privileged = any("write" in v for v in own) if own else top_write
        yield name, block, privileged


class NoCacheWhereItCouldMislead(unittest.TestCase):
    def test_the_parser_sees_the_privileged_jobs(self):
        seen = {
            (path.name, name)
            for path in WORKFLOWS.glob("*.yml")
            for name, _, privileged in jobs(path.read_text())
            if privileged
        }
        for job in [
            ("packages.yml", "publish"),
            ("recipe-bump.yml", "propose"),
            ("pin-bump.yml", "propose"),
            ("codeql.yml", "analyze"),
        ]:
            self.assertIn(job, seen)
        self.assertNotIn(("recipe-bump.yml", "build"), seen)

    def test_the_pattern_catches_a_cache(self):
        for line in [
            "      - uses: actions/cache@55cc # v6",
            "      - uses: Swatinem/rust-cache@d152 # v2",
            "          cache: true",
            "          cache: npm",
        ]:
            self.assertRegex(line, CACHE_USE)
        self.assertNotRegex("          cache: false", CACHE_USE)

    def test_no_privileged_unreviewed_or_published_job_uses_a_cache(self):
        found = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for name, block, privileged in jobs(path.read_text()):
                key = (path.name, name)
                if not (privileged or key in UNREVIEWED_BUILDS or key in PUBLISHED_BUILDS):
                    continue
                found += [f"{path.name} {name}: {line.strip()}" for line in block if CACHE_USE.search(line)]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
