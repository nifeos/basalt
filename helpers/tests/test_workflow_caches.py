"""No job in any workflow restores or saves a cache.

Record §328 lets checks stand in for review, so a release no person has read builds here: in the
recipe bump's `build` job, in packages' `build` on its pull request, and again in packages' `build`
on main once it merges. A hosted runner gives that code sudo, so it can read the runner's cache
token and save entries whatever the job declares, and the run on main saves them where every
branch restores them. Scoping by ref cannot help while the merged build runs on main, so the only
cache nobody could have planted is none: a check that restored one could pass falsely and merge a
pull request with no person. The cost is QEMU built from source in every job that boots, and the
gate's tree built cold.

Workflows are read as text, since the gate's Python has no YAML parser; the layout is the one
every workflow here uses (two-space indent, jobs under a top-level `jobs:`).
"""

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"

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


def triggers(text):
    """The event names under a workflow's top-level `on:`."""
    lines = code_lines(text.splitlines())
    start = next(i for i, line in enumerate(lines) if line.startswith("on:"))
    names = []
    for line in lines[start + 1 :]:
        if TOP_KEY.match(line):
            break
        match = re.match(r"^  ([a-z_]+):", line)
        if match:
            names.append(match.group(1))
    return names


class ReleaseCodeNeverRunsOnMain(unittest.TestCase):
    """A run on main saves cache entries every branch restores, so the scheduled bump only plans,
    and the release builds in recipe-build.yml, which only a push to `recipe-build/<name>` or a
    pull request's dry run starts."""

    def test_the_build_workflow_starts_only_off_main(self):
        text = (WORKFLOWS / "recipe-build.yml").read_text()
        self.assertEqual(triggers(text), ["push", "pull_request"])
        self.assertIn('    branches: ["recipe-build/**"]', text)

    def test_the_scheduled_bump_builds_nothing(self):
        text = "\n".join(code_lines((WORKFLOWS / "recipe-bump.yml").read_text().splitlines()))
        for needle in ["packages.py build", "cargo", "note-tool", "script/bootstrap", "repository:", "rewrite"]:
            self.assertNotIn(needle, text)


class NoCacheAnywhere(unittest.TestCase):
    def test_the_parser_sees_the_privileged_jobs(self):
        seen = {
            (path.name, name)
            for path in WORKFLOWS.glob("*.yml")
            for name, _, privileged in jobs(path.read_text())
            if privileged
        }
        for job in [
            ("publish.yml", "publish"),
            ("recipe-build.yml", "propose"),
            ("pin-bump.yml", "propose"),
            ("codeql.yml", "analyze"),
        ]:
            self.assertIn(job, seen)
        self.assertNotIn(("recipe-build.yml", "build"), seen)

    def test_the_pattern_catches_a_cache(self):
        for line in [
            "      - uses: actions/cache@55cc # v6",
            "      - uses: Swatinem/rust-cache@d152 # v2",
            "          cache: true",
            "          cache: npm",
        ]:
            self.assertRegex(line, CACHE_USE)
        self.assertNotRegex("          cache: false", CACHE_USE)

    def test_no_job_uses_a_cache(self):
        found = []
        for path in sorted(WORKFLOWS.glob("*.yml")):
            for name, block, _ in jobs(path.read_text()):
                found += [f"{path.name} {name}: {line.strip()}" for line in block if CACHE_USE.search(line)]
        self.assertEqual(found, [])

    def test_every_job_is_seen(self):
        seen = {(path.name, name) for path in WORKFLOWS.glob("*.yml") for name, _, _ in jobs(path.read_text())}
        for job in [("gate.yml", "nife"), ("packages.yml", "install"), ("packages.yml", "composed")]:
            self.assertIn(job, seen)

if __name__ == "__main__":
    unittest.main()
