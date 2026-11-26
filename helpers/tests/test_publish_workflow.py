"""Publishing happens in a run the release's code cannot reach.

`packages`' `build` runs a release's code with sudo, and on main that may be a recipe bump no
person read (record §328). That code can take the run's artifact token, which outlives the job, and
replace any artifact of that run, including one a later job of the same run uploads and another
downloads. So nothing in packages.yml publishes: publish.yml, a separate run started by
`workflow_run`, verifies the packages run's artifact against main, deploys, and backs up. These
tests read the workflows as text, as test_workflow_caches.py does, and fail if that shape is lost.

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

    def test_backup_refuses_a_site_publish_did_not_write(self):
        block = job(PUBLISH, "backup")
        names = [step[0].strip() for step in steps(block)]
        verify = next(i for i, s in enumerate(steps(block)) if "packages.py site-digest" in "\n".join(s))
        push = next(i for i, s in enumerate(steps(block)) if "BACKUP_DEPLOY_KEY" in "\n".join(s))
        self.assertLess(verify, push, names)
        text = "\n".join(steps(block)[verify])
        self.assertIn('[ "$got" = "$SITE_DIGEST" ]', text)
        self.assertIn("${{ needs.publish.outputs.site }}", "\n".join(block))


if __name__ == "__main__":
    unittest.main()
