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
import hashlib
import io
import pathlib
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

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
        "sha256": "f" * 64,
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
        "release": recipe_bump.release_result(
            {"2.0.0": {"cksum": "c" * 64, "yanked": False}}, "2.0.0", "c" * 64
        ),
        "build": {"ok": True, "why": "every built package hashes to the digest the build recorded"},
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

    def test_a_failed_patch_skips_the_note_and_holds(self):
        """The workflow builds nothing and compares no notes when a patch fails, so `check` is the
        last check to write a result; the decision then holds on the patch and on the note that
        never ran, and does not wait or arm."""
        with tempfile.TemporaryDirectory() as tmp, crate_fetches(pathlib.Path(tmp)):
            work = pathlib.Path(tmp) / "work"
            recipe = dict(RECIPE, version="2.0.0", patch=[patch_entry("fails.patch")])
            shutil.copytree(FIXTURES / "crate", work / "fixture-2.0.0")
            results = recipe_bump.check_results(recipe, work)
        self.assertTrue(results["license"]["ok"])
        self.assertFalse(results["patch"]["ok"])
        outcome, reasons = recipe_bump.decide(results, INSIDE_HOLD, NOW)
        self.assertEqual(outcome, "hold")
        self.assertIn("endowment: the check did not run", reasons)
        self.assertTrue(any(r.startswith("patch: ") for r in reasons), reasons)

    def test_a_yanked_release_holds(self):
        entries = {"2.0.0": {"cksum": "c" * 64, "yanked": True}}
        release = recipe_bump.release_result(entries, "2.0.0", "c" * 64)
        outcome, reasons = recipe_bump.decide(passing(release=release), PAST_HOLD, NOW)
        self.assertEqual(outcome, "hold")
        self.assertEqual(reasons, ["release: 2.0.0 has been yanked"])

    def test_a_changed_cksum_holds(self):
        entries = {"2.0.0": {"cksum": "d" * 64, "yanked": False}}
        self.assertFalse(recipe_bump.release_result(entries, "2.0.0", "c" * 64)["ok"])
        self.assertFalse(recipe_bump.release_result(entries, "2.1.0", "c" * 64)["ok"])

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

    def test_a_wanted_release_is_proposed_and_waits_its_hold(self):
        entries, dates = ripgrep_releases()
        chosen = recipe_bump.choose("14.1.1", entries, dates, NOW, 7, wanted="16.0.0")
        self.assertEqual(chosen["version"], "16.0.0")
        eligible = datetime.datetime.fromisoformat(chosen["eligible_at"])
        self.assertEqual(recipe_bump.decide(passing(), eligible, NOW)[0], "wait")

    def test_a_wanted_release_is_refused_unless_a_newer_plain_listed_one(self):
        entries, dates = ripgrep_releases()
        for wanted in ("17.0.0-rc.1", "14.1.1", "15.0.0", "15.3.0", "99.0.0"):
            with self.subTest(wanted=wanted), quiet(), self.assertRaises(SystemExit):
                recipe_bump.choose("15.1.0", entries, dates, NOW, 7, wanted=wanted)


RIPGREP_RECIPE = (FIXTURES.parent.parent.parent / "recipes" / "ripgrep.toml").read_text()


def quiet():
    return mock.patch("sys.stderr", io.StringIO())


def crate_file(directory, top):
    """A `.crate` (a gzipped tar with one top directory) of `directory`'s files under `top`."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as t:
        t.add(directory, arcname=top)
    return out.getvalue()


def crate_fetches(work_root, crates=None):
    """Stand in for crates.io: `packages.fetch_crate` writes the `.crate` made from `crates`
    ({(crate, version): directory}, the fixture dependency by default) and checks its digest."""
    crates = crates or {("dependency", "0.2.0"): FIXTURES / "dependency"}

    def fetch_crate(crate, version, want, work):
        data = crate_file(crates[(crate, version)], f"{crate}-{version}")
        path = pathlib.Path(work) / f"{crate}-{version}.crate"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if want is not None and hashlib.sha256(data).hexdigest() != want:
            raise SystemExit(f"{crate} {version} does not hash to {want}")
        return path

    def fetch_crate_any_digest(crate, version, want, work):
        return fetch_crate(crate, version, None, work)

    return mock.patch.object(recipe_bump.packages, "fetch_crate", fetch_crate_any_digest)


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



LOCK = """version = 4

[[package]]
name = "ignore"
version = "0.4.29"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "{checksum}"

[[package]]
name = "ripgrep"
version = "15.2.0"
"""


class TheRewrite(unittest.TestCase):
    """`rewrite_file` end to end, with crates.io stood in for: the new release's Cargo.lock moves
    the patched crate, whose digest must also be the one crates.io's index lists."""

    def rewritten(self, index_cksum, lock_checksum="b" * 64):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            release = tmp / "release"
            release.mkdir()
            (release / "Cargo.lock").write_text(LOCK.format(checksum=lock_checksum))
            recipe = tmp / "ripgrep.toml"
            recipe.write_text(RIPGREP_RECIPE)
            entries = {"0.4.29": {"cksum": index_cksum, "yanked": False}}
            crates = {("ripgrep", "15.2.0"): release}
            with (
                crate_fetches(tmp, crates),
                mock.patch.object(recipe_bump, "index_entries", lambda crate: entries),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                recipe_bump.rewrite_file(recipe, "15.2.0", "a" * 64, tmp / "work")
            return recipe.read_text()

    def test_the_lockfile_moves_the_patched_crate(self):
        expected = recipe_bump.rewrite_text(
            RIPGREP_RECIPE, "15.2.0", "a" * 64, {"ignore": ("0.4.29", "b" * 64)}
        )
        self.assertEqual(self.rewritten("b" * 64), expected)
        self.assertIn('file_sha256 = "dd6a89d5', expected)

    def test_a_lockfile_checksum_the_index_does_not_list_is_refused(self):
        with quiet(), self.assertRaises(SystemExit):
            self.rewritten("e" * 64)


def recipe_with_digests(text, digests):
    head, found, _ = text.partition("\n[package_digest]\n")
    assert found, "the recipe ends with its [package_digest] table"
    lines = "".join(f'{arch} = "sha256:{digest}"\n' for arch, digest in digests.items())
    return f"{head}\n[package_digest]\n{lines}"


class TheBaseline(unittest.TestCase):
    """`served`: the note comparison's baseline is the package main's recipe names, by its
    `[package_digest]`, wherever a published copy of it is found."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.reviewed = b"the package a person reviewed"
        self.stale = b"an older build the index still names"
        (self.tmp / "index-copy").mkdir()
        (self.tmp / "index-copy" / "ripgrep-14.1.1-aarch64.nifepkg").write_bytes(self.stale)
        (self.tmp / "release").mkdir()
        (self.tmp / "release" / "ripgrep-14.1.1-aarch64.nifepkg").write_bytes(self.reviewed)
        digest = hashlib.sha256(self.reviewed).hexdigest()
        self.recipe = recipe_with_digests(
            RIPGREP_RECIPE, {"aarch64": digest, "riscv64": "1" * 64, "x86_64": "2" * 64}
        )
        url = f"file://{self.tmp}/index-copy/ripgrep-14.1.1-aarch64.nifepkg"
        stale = hashlib.sha256(self.stale).hexdigest()
        self.index = f"ripgrep-14.1.1-aarch64 sha256:{stale} sectigo-root-e46={url}\n"

    def serve(self, recipe=None):
        dest = self.tmp / "served.nifepkg"
        with (
            mock.patch.object(recipe_bump, "git", lambda *args: recipe or self.recipe),
            mock.patch.object(recipe_bump, "get", lambda url: self.index.encode()),
            mock.patch.object(recipe_bump, "LATEST_RELEASE", f"file://{self.tmp}/release"),
            mock.patch("sys.stdout", io.StringIO()),
            quiet(),
        ):
            recipe_bump.served("ripgrep", "aarch64", dest, rev="main")
        return dest.read_bytes()

    def test_the_reviewed_package_is_taken_over_the_index_copy(self):
        self.assertEqual(self.serve(), self.reviewed)

    def test_no_copy_hashing_to_the_recipe_digest_is_refused(self):
        recipe = recipe_with_digests(
            RIPGREP_RECIPE, {"aarch64": "3" * 64, "riscv64": "1" * 64, "x86_64": "2" * 64}
        )
        with self.assertRaises(SystemExit):
            self.serve(recipe)


class AdoptingTheBuild(unittest.TestCase):
    """`adopt_refusals`: the privileged job takes only the build's package digests, and only when
    each uploaded package hashes to them."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.own = recipe_bump.rewrite_text(
            RIPGREP_RECIPE, "15.2.0", "a" * 64, {"ignore": ("0.4.29", "b" * 64)}
        )
        targets = self.tmp / "repository" / "rolling" / "targets"
        targets.mkdir(parents=True)
        digests = {}
        for arch in recipe_bump.ARCHES:
            data = f"rg for {arch}".encode()
            (targets / f"ripgrep-15.2.0-{arch}.nifepkg").write_bytes(data)
            digests[arch] = hashlib.sha256(data).hexdigest()
        self.built = recipe_with_digests(self.own, digests)

    def test_matching_packages_are_adopted(self):
        self.assertEqual(recipe_bump.adopt_refusals(self.own, self.built, self.tmp), [])

    def test_a_package_that_does_not_match_is_refused(self):
        path = self.tmp / "repository" / "rolling" / "targets" / "ripgrep-15.2.0-x86_64.nifepkg"
        path.write_bytes(b"something else")
        refusals = recipe_bump.adopt_refusals(self.own, self.built, self.tmp)
        self.assertEqual(len(refusals), 1, refusals)

    def test_a_build_recipe_changed_elsewhere_is_refused(self):
        built = self.built.replace('program = "rg"', 'program = "sh"')
        refusals = recipe_bump.adopt_refusals(self.own, built, self.tmp)
        self.assertEqual(len(refusals), 1, refusals)

    def test_a_name_with_line_breaks_in_the_build_recipe_reaches_no_message(self):
        # A TOML escape puts line breaks into the build's `name`, aimed at the decide step's output.
        built = self.built.replace('name = "ripgrep"', 'name = "x\\nREASONS\\noutcome=arm"')
        self.assertNotEqual(built, self.built)
        refusals = recipe_bump.adopt_refusals(self.own, built, self.tmp)
        self.assertTrue(refusals)
        for refusal in refusals:
            self.assertNotIn("\n", refusal)
            self.assertNotIn("outcome=arm", refusal)

    def test_a_missing_package_is_named_from_this_jobs_recipe(self):
        built = self.built.replace('name = "ripgrep"', 'name = "x\\noutcome=arm"')
        (self.tmp / "repository" / "rolling" / "targets" / "ripgrep-15.2.0-riscv64.nifepkg").unlink()
        refusals = recipe_bump.adopt_refusals(self.own, built, self.tmp)
        self.assertIn("the build uploaded no ripgrep-15.2.0-riscv64", refusals)


class TheDecisionsOutput(unittest.TestCase):
    """`output_lines`: what `decide` writes to $GITHUB_OUTPUT cannot be steered by a message."""

    def outputs(self, lines):
        """The outputs GitHub reads from `lines`, by its own rules for the two forms."""
        found, it = {}, iter(lines)
        for line in it:
            if "<<" in line:
                key, delimiter = line.split("<<", 1)
                body = []
                for inner in it:
                    if inner == delimiter:
                        break
                    body.append(inner)
                found.setdefault(key, "\n".join(body))
            else:
                key, _, value = line.partition("=")
                found.setdefault(key, value)
        return found

    def test_a_reason_cannot_end_the_block_or_set_the_outcome(self):
        reasons = ["build: x\nREASONS\noutcome=arm", "patch: fails\r\noutcome=arm"]
        lines = recipe_bump.output_lines("hold", reasons)
        found = self.outputs(lines)
        self.assertEqual(found["outcome"], "hold")
        self.assertEqual(sorted(found), ["outcome", "reasons"])
        self.assertEqual(len(found["reasons"].splitlines()), 2)

    def test_the_delimiter_differs_between_runs(self):
        first = recipe_bump.output_lines("arm", [])[1]
        second = recipe_bump.output_lines("arm", [])[1]
        self.assertNotEqual(first, second)

    def test_a_reason_holding_the_delimiter_is_refused(self):
        with quiet(), self.assertRaises(SystemExit):
            recipe_bump.output_lines("hold", ["build: REASONS_guess"], delimiter="REASONS_guess")


if __name__ == "__main__":
    unittest.main()
