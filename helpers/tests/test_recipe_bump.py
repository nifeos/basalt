"""Tests of the recipe bump's decision logic (helpers/recipe_bump.py), against the fixtures beside
this file. No network: each check is run on fixture files, and the releases are ripgrep's as
crates.io listed them on 2026-11-25 (UTC).

Run from the repository root:

    python3 -m unittest discover -s helpers/tests

The note comparison's own cases (same, narrower, wider, undecodable) are tested in
tools/manifest_note_compare against nife's encoder; here its output is a fixture, read as the
workflow reads it.

Name provisional.
"""

import datetime
import pathlib
import sys
import unittest

HELPERS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HELPERS))

import recipe_bump  # found through the line above

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
UTC = datetime.UTC

RECIPE = {
    "name": "fixture",
    "version": "1.0.0",
    "crate": "fixture",
    "license": "Unlicense OR MIT",
    "license_files": ["COPYING", "LICENSE-MIT", "UNLICENSE"],
}
NOW = datetime.datetime(2026, 11, 25, 12, tzinfo=UTC)
PAST_HOLD = datetime.datetime(2026, 7, 22, tzinfo=UTC)
INSIDE_HOLD = datetime.datetime(2026, 11, 29, 23, tzinfo=UTC)


def patch_entry(name):
    path = FIXTURES / name
    return {
        "crate": "dependency",
        "version": "0.2.0",
        "file": str(path.relative_to(recipe_bump.BASALT)),
        "file_sha256": recipe_bump.packages.sha256(path),
    }


def passing(**overrides):
    """Results in which every check passes, with `overrides` replacing some."""
    results = {
        "note": recipe_bump.note_result(0, "rg: same\n"),
        "license": recipe_bump.license_result(RECIPE, FIXTURES / "crate"),
        "patch": recipe_bump.patch_result(patch_entry("applies.patch"), FIXTURES / "dependency"),
        "confined": {"ok": True, "why": "only the version and digest lines changed"},
    }
    results.update(overrides)
    return results


class TheSixCases(unittest.TestCase):
    """The outcomes record §328 names, each from the check that decides it."""

    def test_an_unchanged_note_passes(self):
        outcome, _ = recipe_bump.decide(passing(), PAST_HOLD, NOW)
        self.assertEqual(outcome, "arm")

    def test_a_narrowed_note_passes(self):
        note = recipe_bump.note_result(0, "rg: narrower: it no longer asks for `clock`\n")
        outcome, reasons = recipe_bump.decide(passing(note=note), PAST_HOLD, NOW)
        self.assertEqual(outcome, "arm")
        self.assertTrue(any("no longer asks for `clock`" in r for r in reasons), reasons)

    def test_a_widened_note_holds(self):
        output = "rg: held: it asks for `network`, which the served one does not\n"
        note = recipe_bump.note_result(1, output)
        outcome, reasons = recipe_bump.decide(passing(note=note), PAST_HOLD, NOW)
        self.assertEqual(outcome, "hold")
        self.assertTrue(reasons[0].startswith("endowment: "), reasons)

    def test_a_note_that_cannot_be_compared_holds(self):
        note = recipe_bump.note_result(2, "")
        outcome, reasons = recipe_bump.decide(passing(note=note), PAST_HOLD, NOW)
        self.assertEqual(outcome, "hold")
        self.assertIn("could not be compared", reasons[0])

    def test_a_changed_license_holds(self):
        license = recipe_bump.license_result(dict(RECIPE, license="MIT"), FIXTURES / "crate")
        outcome, reasons = recipe_bump.decide(passing(license=license), PAST_HOLD, NOW)
        self.assertEqual(outcome, "hold")
        self.assertTrue(reasons[0].startswith("license: upstream declares"), reasons)

    def test_a_missing_license_file_holds(self):
        recipe = dict(RECIPE, license_files=["COPYING", "LICENSE-APACHE"])
        license = recipe_bump.license_result(recipe, FIXTURES / "crate")
        self.assertFalse(license["ok"])
        self.assertIn("LICENSE-APACHE", license["why"])

    def test_a_failing_patch_holds(self):
        patch = recipe_bump.patch_result(patch_entry("fails.patch"), FIXTURES / "dependency")
        outcome, reasons = recipe_bump.decide(passing(patch=patch), PAST_HOLD, NOW)
        self.assertEqual(outcome, "hold")
        self.assertTrue(reasons[0].startswith("patch: "), reasons)

    def test_an_edited_patch_file_holds(self):
        entry = dict(patch_entry("applies.patch"), file_sha256="0" * 64)
        patch = recipe_bump.patch_result(entry, FIXTURES / "dependency")
        self.assertFalse(patch["ok"])
        self.assertIn("not the file the recipe reviewed", patch["why"])

    def test_a_release_younger_than_the_hold_waits(self):
        outcome, reasons = recipe_bump.decide(passing(), INSIDE_HOLD, NOW)
        self.assertEqual(outcome, "wait")
        self.assertIn("2026-11-29", reasons[0])

    def test_a_held_young_release_holds_rather_than_waits(self):
        note = recipe_bump.note_result(1, "rg: held: it asks for `network`\n")
        outcome, _ = recipe_bump.decide(passing(note=note), INSIDE_HOLD, NOW)
        self.assertEqual(outcome, "hold")

    def test_a_check_that_did_not_run_holds(self):
        results = passing()
        del results["note"]
        outcome, reasons = recipe_bump.decide(results, PAST_HOLD, NOW)
        self.assertEqual(outcome, "hold")
        self.assertEqual(reasons, ["endowment: the check did not run"])


def ripgrep_releases():
    """ripgrep's index lines and publication dates from 14.1.1 on, plus a pre-release and a yanked
    one that must never be proposed."""
    rows = [
        ("14.1.1", "2024-09-09T02:30:38+00:00", False),
        ("15.0.0", "2025-10-16T03:30:12+00:00", False),
        ("15.1.0", "2025-10-22T13:00:06+00:00", False),
        ("15.2.0", "2026-07-15T16:28:46+00:00", False),
        ("15.3.0", "2026-08-01T00:00:00+00:00", True),
        ("16.0.0", "2026-11-22T23:02:08+00:00", False),
        ("17.0.0-rc.1", "2026-11-01T00:00:00+00:00", False),
    ]
    entries = {v: {"cksum": f"{i:064x}", "yanked": y} for i, (v, _, y) in enumerate(rows)}
    dates = {v: datetime.datetime.fromisoformat(d) for v, d, _ in rows}
    return entries, dates


class ChoosingARelease(unittest.TestCase):
    def test_the_newest_release_past_the_hold_is_proposed(self):
        entries, dates = ripgrep_releases()
        chosen = recipe_bump.choose("14.1.1", entries, dates, NOW, 7)
        self.assertEqual(chosen["version"], "15.2.0")
        self.assertEqual(chosen["sha256"], entries["15.2.0"]["cksum"])
        self.assertTrue(chosen["waiting"].startswith("16.0.0 (eligible 2026-11-29"))

    def test_a_release_that_has_aged_in_replaces_it(self):
        entries, dates = ripgrep_releases()
        later = datetime.datetime(2026, 11, 30, tzinfo=UTC)
        chosen = recipe_bump.choose("14.1.1", entries, dates, later, 7)
        self.assertEqual(chosen["version"], "16.0.0")
        self.assertNotIn("waiting", chosen)

    def test_only_young_releases_propose_the_newest_which_waits(self):
        entries, dates = ripgrep_releases()
        chosen = recipe_bump.choose("15.2.0", entries, dates, NOW, 7)
        self.assertEqual(chosen["version"], "16.0.0")
        eligible = datetime.datetime.fromisoformat(chosen["eligible_at"])
        self.assertEqual(recipe_bump.decide(passing(), eligible, NOW)[0], "wait")

    def test_nothing_newer_proposes_nothing(self):
        entries, dates = ripgrep_releases()
        self.assertIsNone(recipe_bump.choose("16.0.0", entries, dates, NOW, 7))


RIPGREP_RECIPE = (FIXTURES.parent.parent.parent / "recipes" / "ripgrep.toml").read_text()


class RewritingAndTheDiffGuard(unittest.TestCase):
    def bumped(self):
        return recipe_bump.rewrite_text(
            RIPGREP_RECIPE, "15.2.0", "a" * 64, {"ignore": ("0.4.29", "b" * 64)}
        )

    def test_the_rewrite_moves_the_version_digest_and_patched_crate_only(self):
        old, new = RIPGREP_RECIPE.splitlines(), self.bumped().splitlines()
        changed = [(a, b) for a, b in zip(old, new) if a != b]
        self.assertEqual(len(old), len(new))
        self.assertEqual(len(changed), 4, changed)
        self.assertIn('file_sha256 = "dd6a89d5', self.bumped())

    def test_the_bump_is_confined(self):
        changed = [("M", "recipes/ripgrep.toml")]
        self.assertEqual(
            recipe_bump.confined_refusals("ripgrep", changed, RIPGREP_RECIPE, self.bumped()), []
        )

    def test_another_file_is_refused(self):
        changed = [("M", "recipes/ripgrep.toml"), ("M", "patches/ripgrep-ignore-walk.patch")]
        refusals = recipe_bump.confined_refusals("ripgrep", changed, RIPGREP_RECIPE, self.bumped())
        self.assertEqual(len(refusals), 1, refusals)

    def test_another_key_is_refused(self):
        for old, new in [
            ('program = "rg"', 'program = "sh"'),
            ('file_sha256 = "dd', 'file_sha256 = "ee'),
            ('license = "Unlicense OR MIT"', 'license = "MIT"'),
        ]:
            text = self.bumped().replace(old, new)
            changed = [("M", "recipes/ripgrep.toml")]
            with self.subTest(new=new):
                self.assertTrue(
                    recipe_bump.confined_refusals("ripgrep", changed, RIPGREP_RECIPE, text)
                )


if __name__ == "__main__":
    unittest.main()
