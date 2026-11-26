"""Tests of `helpers/packages.py publish`: the site is written from main's recipes, and the build's
artifact is believed for nothing they record.

The build runs a release's code with sudo, so a process it leaves behind can rewrite the artifact
after the build's own digest check: a package together with the index line that vouches for it.
Each test here lays out such an artifact against a small checkout of main in a temporary directory
(its own recipes, pins.toml and patch) and runs `publish` on it. No network, no nife.

Run from the repository root:

    python3 -m unittest discover -s helpers/tests

Name provisional.
"""

import hashlib
import io
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest import mock

HELPERS = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HELPERS))

import packages  # found through the line above

RELEASE = "https://github.com/example/basalt/releases/download/rolling-2026-11-26-0000000"
COMMIT = "1" * 40
UPSTREAM = b"the crate as crates.io serves it"
DEPENDENCY = b"the patched dependency as crates.io serves it"
PATCH = b"--- a/src/lib.rs\n+++ b/src/lib.rs\n"


def digest(data):
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def package_bytes(arch):
    return f"fixture 1.0.0 for {arch}".encode()


class Publishing(unittest.TestCase):
    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.main = self.tmp / "main"
        (self.main / "recipes").mkdir(parents=True)
        (self.main / "patches").mkdir()
        (self.main / "helpers").mkdir()
        (self.main / "patches" / "fixture-dependency.patch").write_bytes(PATCH)
        shutil.copyfile(HELPERS / "packages.py", self.main / "helpers" / "packages.py")
        (self.main / "pins.toml").write_text(
            f'[nife]\nrepository = "https://github.com/example/nife"\ncommit = "{COMMIT}"\n'
        )
        table = "".join(f'{a} = "{digest(package_bytes(a))}"\n' for a in packages.ARCHITECTURES)
        (self.main / "recipes" / "fixture.toml").write_text(
            'name = "fixture"\nversion = "1.0.0"\ncrate = "fixture"\n'
            f'sha256 = "{hashlib.sha256(UPSTREAM).hexdigest()}"\nprogram = "fixture"\n'
            'license = "MIT"\nlicense_files = ["LICENSE"]\n\n'
            '[[patch]]\ncrate = "dependency"\nversion = "0.2.0"\n'
            f'sha256 = "{hashlib.sha256(DEPENDENCY).hexdigest()}"\n'
            'file = "patches/fixture-dependency.patch"\n'
            f'file_sha256 = "{hashlib.sha256(PATCH).hexdigest()}"\n\n'
            f"[package_digest]\n{table}"
        )
        # The base publishes nothing; publish must pass over it.
        (self.main / "recipes" / "base.toml").write_text(
            'name = "base"\nversion = "0.1.0"\nlicense = "MIT"\nlicense_files = ["LICENSE"]\n\n'
            '[base]\nimage = "packages/base-image.toml"\n'
        )
        self.enterContext(mock.patch.object(packages, "BASALT", self.main))
        self.repository = self.tmp / "repository"
        self.site = self.tmp / "site"
        self.lay_out_the_build()

    def lay_out_the_build(self):
        """The artifact an honest build uploads."""
        rolling = self.repository / packages.CHANNEL
        for d in ("metadata", "targets", "sources"):
            (rolling / d).mkdir(parents=True, exist_ok=True)
        lines = []
        for arch in packages.ARCHITECTURES:
            stem = f"fixture-1.0.0-{arch}"
            (rolling / "targets" / f"{stem}.nifepkg").write_bytes(package_bytes(arch))
            lines.append(f"{stem} {digest(package_bytes(arch))}\n")
        (rolling / "metadata" / "stand-in-index").write_text("".join(lines))
        self.write_source_archive(UPSTREAM, DEPENDENCY)

    def write_source_archive(self, upstream, dependency, note=None):
        path = self.main / "recipes" / "fixture.toml"
        recipe = packages.tomllib.loads(path.read_text())
        top, members = packages.source_members(
            recipe, path, [("fixture-1.0.0.crate", upstream), ("dependency-0.2.0.crate", dependency)], COMMIT
        )
        if note is not None:
            members = [(n, note if n == "BUILT-FROM" else d) for n, d in members]
        dest = self.repository / packages.CHANNEL / "sources" / "fixture-1.0.0-source.tar.gz"
        packages.write_source_tarball(top, members, dest)
        return dest

    def publish(self):
        with mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            packages.publish(self.repository, self.site, RELEASE)

    def refused(self):
        with self.assertRaises(SystemExit):
            self.publish()
        self.assertFalse((self.site / packages.CHANNEL / "metadata" / "stand-in-index").exists())

    def test_an_honest_build_is_published(self):
        self.publish()
        index = (self.site / packages.CHANNEL / "metadata" / "stand-in-index").read_text().splitlines()
        self.assertEqual(len(index), len(packages.ARCHITECTURES))
        for arch, line in zip(packages.ARCHITECTURES, index):
            stem = f"fixture-1.0.0-{arch}"
            self.assertEqual(line, f"{stem} {digest(package_bytes(arch))} sectigo-root-e46={RELEASE}/{stem}.nifepkg")
            self.assertEqual((self.site / packages.CHANNEL / "targets" / f"{stem}.nifepkg").read_bytes(),
                             package_bytes(arch))
        built = (self.repository / packages.CHANNEL / "sources" / "fixture-1.0.0-source.tar.gz").read_bytes()
        served = (self.site / packages.CHANNEL / "sources" / "fixture-1.0.0-source.tar.gz").read_bytes()
        self.assertEqual(served, built)

    def test_a_package_rewritten_with_its_index_line_is_refused(self):
        # The exploit: the package and the line vouching for it, rewritten alike after the build's
        # check. The artifact agrees with itself; only main's recipe can tell.
        rolling = self.repository / packages.CHANNEL
        planted = b"what the leftover process wrote"
        (rolling / "targets" / "fixture-1.0.0-x86_64.nifepkg").write_bytes(planted)
        index = rolling / "metadata" / "stand-in-index"
        index.write_text(index.read_text().replace(digest(package_bytes("x86_64")), digest(planted)))
        self.refused()

    def test_a_package_rewritten_alone_is_refused(self):
        (self.repository / packages.CHANNEL / "targets" / "fixture-1.0.0-aarch64.nifepkg").write_bytes(b"x")
        self.refused()

    def test_a_package_no_recipe_names_is_refused(self):
        rolling = self.repository / packages.CHANNEL
        extra = b"a base package, say"
        (rolling / "targets" / "base-shell-0.1.0-x86_64.nifepkg").write_bytes(extra)
        with (rolling / "metadata" / "stand-in-index").open("a") as index:
            index.write(f"base-shell-0.1.0-x86_64 {digest(extra)}\n")
        self.refused()

    def test_a_file_beside_the_packages_is_refused(self):
        (self.repository / packages.CHANNEL / "targets" / "other.nifepkg").write_bytes(b"x")
        self.refused()

    def test_a_dropped_index_line_is_refused(self):
        index = self.repository / packages.CHANNEL / "metadata" / "stand-in-index"
        index.write_text("".join(index.read_text().splitlines(keepends=True)[:-1]))
        self.refused()

    def test_a_rewritten_upstream_file_in_the_source_archive_is_refused(self):
        self.write_source_archive(b"other source", DEPENDENCY)
        self.refused()

    def test_the_rest_of_the_source_archive_is_main_s_own(self):
        honest = self.write_source_archive(UPSTREAM, DEPENDENCY).read_bytes()
        self.write_source_archive(UPSTREAM, DEPENDENCY, note=b"built from somewhere else\n")
        self.publish()
        served = (self.site / packages.CHANNEL / "sources" / "fixture-1.0.0-source.tar.gz").read_bytes()
        self.assertEqual(served, honest)


class TheSiteDigest(unittest.TestCase):
    """`site_digest`: what `publish` deploys and what `backup` copies are the same tree."""

    def setUp(self):
        self.site = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.site)
        (self.site / "rolling" / "metadata").mkdir(parents=True)
        (self.site / "rolling" / "metadata" / "stand-in-index").write_text("a sha256:00\n")
        (self.site / "index.html").write_text("<p>basalt</p>\n")
        self.digest = packages.site_digest(self.site)

    def test_the_same_tree_has_the_same_digest(self):
        self.assertRegex(self.digest, r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(packages.site_digest(self.site), self.digest)

    def test_a_changed_file_moves_it(self):
        (self.site / "index.html").write_text("<p>elsewhere</p>\n")
        self.assertNotEqual(packages.site_digest(self.site), self.digest)

    def test_an_added_file_moves_it(self):
        (self.site / "rolling" / "extra").write_bytes(b"")
        self.assertNotEqual(packages.site_digest(self.site), self.digest)

    def test_a_renamed_file_moves_it(self):
        (self.site / "index.html").rename(self.site / "other.html")
        self.assertNotEqual(packages.site_digest(self.site), self.digest)

    def test_a_symbolic_link_is_refused(self):
        (self.site / "link").symlink_to("/etc/hostname")
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            packages.site_digest(self.site)


if __name__ == "__main__":
    unittest.main()
