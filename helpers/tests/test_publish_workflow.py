"""Publishing happens in a run the release's code cannot reach.

`packages`' `build` runs a release's code with sudo, and on main that may be a recipe bump no
person read (record §328). That code can take the run's artifact token, which outlives the job, and
replace any artifact of that run, including one a later job of the same run uploads and another
downloads. So nothing in packages.yml publishes: publish.yml, a separate run started by
`workflow_run`, verifies the packages run's artifact against main, deploys, and backs up. These
tests read the workflows as text, as test_workflow_caches.py does, and fail if that shape is lost.
They also hold `reproduce` to failing, not skipping, when `build` fails, and `install` and
`composed` when `reproduce` fails, since a skipped required check counts as passing.

Name provisional.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_workflow_caches import (  # found through the line above
    WORKFLOWS,
    code_lines,
    jobs,
    triggers,
)

PACKAGES = (WORKFLOWS / "packages.yml").read_text()
PUBLISH = (WORKFLOWS / "publish.yml").read_text()


def job(text, name):
    return next(block for found, block, _ in jobs(text) if found == name)


def steps(block):
    """Each step of a job, as its lines."""
    found, current = [], None
    for line in block:
        if re.match(r"^      - ", line):
            current = [line]
            found.append(current)
        elif current is not None:
            current.append(line)
    return found


class NothingPublishesFromTheBuildRun(unittest.TestCase):
    def test_no_packages_job_can_write_or_deploy(self):
        names = [name for name, _, _ in jobs(PACKAGES)]
        self.assertEqual(names, ["build", "reproduce", "install", "composed"])
        self.assertEqual([name for name, _, privileged in jobs(PACKAGES) if privileged], [])
        code = "\n".join(code_lines(PACKAGES.splitlines()))
        for needle in ["deploy-pages", "upload-pages-artifact", "BACKUP_DEPLOY_KEY", "packages.py publish",
                       "gh release", "write"]:
            self.assertNotIn(needle, code)


class ThePublishRun(unittest.TestCase):
    def test_only_a_finished_packages_run_starts_it(self):
        self.assertEqual(triggers(PUBLISH), ["workflow_run"])
        self.assertRegex(PUBLISH, r"workflows: \[packages\]\n\s+types: \[completed\]")

    def test_it_takes_only_a_green_run_on_main_in_this_repository(self):
        condition = " ".join("\n".join(job(PUBLISH, "publish")).split("if: >-", 1)[1].split("runs-on:")[0].split())
        for needle in [
            "github.event.workflow_run.conclusion == 'success'",
            "github.event.workflow_run.head_branch == 'main'",
            "github.event.workflow_run.event == 'push'",
            "github.event.workflow_run.repository.full_name == github.repository",
            "github.event.workflow_run.head_repository.full_name == github.repository",
        ]:
            self.assertIn(needle, condition)
        self.assertNotIn("pull_request", condition)

    def test_every_checkout_is_main_with_no_credential(self):
        for name, block, _ in jobs(PUBLISH):
            for step in steps(block):
                if any("actions/checkout@" in line for line in step):
                    text = "\n".join(step)
                    self.assertIn("ref: main", text, name)
                    self.assertIn("persist-credentials: false", text, name)
        self.assertNotRegex(PUBLISH, r"ref:\s*\$\{\{\s*github\.event\.workflow_run")

    def test_only_a_qualifying_publish_enters_the_concurrency_group(self):
        top = PUBLISH.split("\njobs:", 1)[0]
        self.assertNotRegex(top, r"(?m)^concurrency:")
        text = "\n".join(job(PUBLISH, "publish"))
        self.assertLess(text.index("if: >-"), text.index("concurrency:"))
        self.assertRegex(text, r"concurrency:\n\s+group: publish\n\s+cancel-in-progress: false")

    def test_publish_checks_main_is_the_commit_built(self):
        text = "\n".join(job(PUBLISH, "publish"))
        self.assertIn('[ "$head" != "$BUILT" ]', text)

    def test_the_build_artifact_is_taken_by_run_id_and_verified(self):
        text = "\n".join(job(PUBLISH, "publish"))
        self.assertIn("run-id: ${{ github.event.workflow_run.id }}", text)
        order = [text.index(n) for n in ["name: repository", "packages.py publish", "packages.py site-digest",
                                          "gh release create", "upload-pages-artifact"]]
        self.assertEqual(order, sorted(order))
        self.assertNotIn("repository/rolling", text)

    def test_no_job_runs_upstream_code(self):
        code = "\n".join(code_lines(PUBLISH.splitlines()))
        for needle in ["packages.py build", "packages.py base", "cargo", "script/", "nife_path", "repository: ${{"]:
            self.assertNotIn(needle, code)

    def test_a_rerun_completes_a_half_made_release(self):
        step = next(s for s in steps(job(PUBLISH, "publish")) if any("gh release download" in l for l in s))
        text = "\n".join(step)
        self.assertIn('if [ ! -e "$e" ]; then', text)
        self.assertNotIn('[[ "$f" == *-source.tar.gz ]]', text)
        self.assertLess(text.index('if [ ! -e "$e" ]'), text.index('gh release upload "$TAG" "$f"'))
        self.assertIn('cmp -s "$f" "$e" || {', text)

    def test_backup_refuses_a_site_publish_did_not_write(self):
        block = job(PUBLISH, "backup")
        names = [step[0].strip() for step in steps(block)]
        verify = next(i for i, s in enumerate(steps(block)) if "packages.py site-digest" in "\n".join(s))
        push = next(i for i, s in enumerate(steps(block)) if "BACKUP_DEPLOY_KEY" in "\n".join(s))
        self.assertLess(verify, push, names)
        text = "\n".join(steps(block)[verify])
        self.assertIn('[ "$got" = "$SITE_DIGEST" ]', text)
        self.assertIn("${{ needs.publish.outputs.site }}", "\n".join(block))


class AFailedReproduceFailsTheRequiredChecks(unittest.TestCase):
    def test_reproduce_fails_rather_than_skips(self):
        block = job(PACKAGES, "reproduce")
        text = "\n".join(block)
        self.assertIn("needs: build", text)
        self.assertIn("if: ${{ !cancelled() }}", text)
        first = "\n".join(steps(block)[0])
        self.assertIn("needs.build.result", first)
        self.assertIn('[ "$BUILD" = success ]', first)
        self.assertIn("exit 1", first)

    def test_install_and_composed_fail_rather_than_skip(self):
        for name in ["install", "composed"]:
            block = job(PACKAGES, name)
            text = "\n".join(block)
            self.assertIn("needs: [build, reproduce]", text)
            self.assertIn("if: ${{ !cancelled() }}", text)
            first = "\n".join(steps(block)[0])
            self.assertIn("needs.build.result", first, name)
            self.assertIn("needs.reproduce.result", first, name)
            self.assertIn('[ "$REPRODUCE" = success ]', first, name)
            self.assertIn("exit 1", first, name)


if __name__ == "__main__":
    unittest.main()
