#!/usr/bin/env python3
"""Build basalt's packages against a nife checkout, and lay them out as a repository.

Usage:
    helpers/packages.py build <nife-checkout> <repository-dir> [--triples T ...]
    helpers/packages.py publish <repository-dir> <site-dir> --release <url>

For each recipe under recipes/, this fetches the pinned source, refuses it unless its SHA-256 is
the recipe's, builds the program for each nife target with that checkout's own toolchain, standard
library and manifest note, and packs it with that checkout's `cargo xtask package`, so the package
format has one producer. Then it writes the repository the way nife's index client reads it (nife
milestone 801 (packages over the internet), calef's Q4 on nife#1884):

    <repository-dir>/rolling/metadata/stand-in-index   one `<stem> sha256:<hex>` line per package
    <repository-dir>/rolling/targets/<stem>.nifepkg     the packages

The index is nife's stand-in encoding until its TUF client exists (nife milestone 858). The channel
name `rolling` is provisional, as nife's `package_index::PROVISIONAL_CHANNEL` is.

`publish` turns a built repository into what is served (calef, 2026-10-12 UTC: "Packages on
Releases, index on Pages"). It writes <site-dir>, the GitHub Pages site behind basalt.nifeos.org,
with the index's every line given one listed location, the package's asset on the GitHub Release
whose download URL prefix is --release:

    <site-dir>/rolling/metadata/stand-in-index   `<stem> sha256:<hex> sectigo-root-e46=<release>/<stem>.nifepkg`
    <site-dir>/rolling/targets/<stem>.nifepkg     basalt's own copy, which nife's jig tries last

The listed location names the root its certificate chains to, `sectigo-root-e46`, which is
github.com's: calef's C2, ruled 2026-10-12 UTC on nife#2048 (nife section 250, clauses 11 to 13).
jig fetches it under that root and follows its one redirect to an ISRG Root X1 host.

The copy under targets/ is nife's section 250, clause 9 (2026-10-10 amendment): "basalt always
keeps a copy under its own targets/, which is tried last". It is the fallback when the Release
cannot be reached; calef has not ruled on dropping it.

Only ripgrep's recipe shape is understood today (a crate, one patched dependency, one program).
That is all basalt builds; a second shape is added with the second recipe that needs it.

Name and interface provisional.
"""

import argparse
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import urllib.request

BASALT = pathlib.Path(__file__).resolve().parent.parent
TRIPLES = ["aarch64-unknown-nife", "riscv64-unknown-nife", "x86_64-unknown-nife"]
CHANNEL = "rolling"


def fail(message):
    print(f"packages: {message}", file=sys.stderr)
    sys.exit(1)


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def fetch_crate(crate, version, want, work):
    """The .crate file, from crates.io, refused unless it hashes to `want`."""
    path = work / f"{crate}-{version}.crate"
    if not path.exists() or sha256(path) != want:
        url = f"https://static.crates.io/crates/{crate}/{crate}-{version}.crate"
        print(f"packages: fetching {url}")
        with urllib.request.urlopen(url, timeout=120) as r:
            path.write_bytes(r.read())
    got = sha256(path)
    if got != want:
        path.unlink()
        fail(f"{crate}-{version}.crate has SHA-256 {got}, not the recipe's {want}; refusing it")
    return path


def unpack(crate_file, work):
    with tarfile.open(crate_file) as t:
        t.extractall(work, filter="data")


def run(cmd, cwd, env=None):
    print("packages: $ " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True)


def build_ripgrep_shape(recipe, nife, work, triples):
    """The crate, its one patched dependency, built for each triple. Returns {triple: elf path}."""
    name, version = recipe["crate"], recipe["version"]
    unpack(fetch_crate(name, version, recipe["sha256"], work), work)
    src = work / f"{name}-{version}"
    config = []
    for patch in recipe.get("patch", []):
        dep = work / f"{patch['crate']}-{patch['version']}"
        patch_file = nife / patch["file"]
        if sha256(patch_file) != patch["file_sha256"]:
            fail(
                f"nife's {patch['file']} has SHA-256 {sha256(patch_file)}, not the recipe's "
                f"{patch['file_sha256']}: the pinned nife changed the patch; review it and the recipe"
            )
        shutil.rmtree(dep, ignore_errors=True)
        unpack(fetch_crate(patch["crate"], patch["version"], patch["sha256"], work), work)
        run(["patch", "-p1", "-s", "-i", patch_file], cwd=dep)
        config += ["--config", f'patch.crates-io.{patch["crate"]}.path="{dep}"']
        # The lock file names the registry copy; a patched path dependency carries no source or
        # checksum, so those two lines go from its entry, as nife's helpers/build-ripgrep.sh does.
        lock = src / "Cargo.lock"
        kept, inside = [], False
        for line in lock.read_text().splitlines():
            if line == f'name = "{patch["crate"]}"':
                inside = True
            elif line == "":
                inside = False
            if inside and (line.startswith("source = ") or line.startswith("checksum = ")):
                continue
            kept.append(line)
        lock.write_text("\n".join(kept) + "\n")
    run(["cargo", "xtask", "std-src"], cwd=nife)
    out = {}
    for triple in triples:
        dest = nife / "target" / "basalt" / recipe["name"] / triple
        dest.mkdir(parents=True, exist_ok=True)
        note = dest / "note.o"
        run(["cargo", "xtask", "foreign-note", triple, note], cwd=nife)
        env = dict(os.environ)
        env.update(
            {
                # nife's release profile carries overflow checks (its notes/overflow-checks.md).
                "CARGO_PROFILE_RELEASE_OVERFLOW_CHECKS": "true",
                "RUSTUP_TOOLCHAIN": str(nife / "target" / "nife-farm"),
                "RUSTFLAGS": " ".join(
                    [
                        f"-Clink-arg=-T{nife}/crates/user_mode_runtime/link.ld",
                        "-Clink-arg=-u_start",
                        "-Clink-arg=--build-id=none",
                        f"-Clink-arg={note}",
                        # Symbols too, not only debug info, as nife strips every program it packs:
                        # a package may be at most 4 MiB (nife's spawnproto::IMAGE_MAX_PAGES).
                        # The manifest note is a PT_NOTE, which stripping keeps.
                        "-Cstrip=symbols",
                        "-Copt-level=s",
                    ]
                ),
            }
        )
        run(
            ["cargo", "build", "--release", "--locked", *config, "-Zjson-target-spec",
             "-Zbuild-std=core,alloc,std,panic_abort",
             "-Zbuild-std-features=compiler-builtins-mem",
             "--target", nife / "targets" / f"{triple}.json"],
            cwd=src,
            env=env,
        )
        elf = dest / recipe["program"]
        shutil.copyfile(src / "target" / triple / "release" / recipe["program"], elf)
        out[triple] = elf
    return out


def pack(recipe, nife, triple, elf):
    """One nife package through nife's own producer. Returns its path."""
    architecture = triple.split("-")[0]
    nife_recipe = elf.parent / f"{recipe['name']}-{architecture}.recipe.toml"
    nife_recipe.write_text(
        f'name = "{recipe["name"]}"\nversion = "{recipe["version"]}"\n'
        f'architecture = "{architecture}"\n\n[[member]]\nname = "bin/{recipe["program"]}"\n'
        f'file = "{elf.relative_to(nife)}"\n'
    )
    run(["cargo", "xtask", "package", nife_recipe], cwd=nife)
    stem = f"{recipe['name']}-{recipe['version']}-{architecture}"
    return stem, nife / "target" / "packages" / f"{stem}.nifepkg"


def build(nife, repository, triples):
    nife = nife.resolve()
    targets = repository / CHANNEL / "targets"
    metadata = repository / CHANNEL / "metadata"
    targets.mkdir(parents=True, exist_ok=True)
    metadata.mkdir(parents=True, exist_ok=True)
    lines = []
    # Outside both checkouts: an unpacked crate with no [workspace] table finds the nearest
    # enclosing workspace and refuses to build inside it.
    work = pathlib.Path(tempfile.gettempdir()) / "basalt-packages"
    work.mkdir(exist_ok=True)
    for path in sorted((BASALT / "recipes").glob("*.toml")):
        recipe = tomllib.loads(path.read_text())
        for triple, elf in build_ripgrep_shape(recipe, nife, work, triples).items():
            stem, package = pack(recipe, nife, triple, elf)
            shutil.copyfile(package, targets / f"{stem}.nifepkg")
            lines.append(f"{stem} sha256:{sha256(package)}\n")
    (metadata / "stand-in-index").write_text("".join(lines))
    print(f"packages: {len(lines)} packages in {repository}")
    print("".join(lines), end="")


# nife's package_index::Location, which jig applies to every listed location: an optional root
# name, then https, a host, a path of printable ASCII up to PATH_MAX (256) bytes. A line jig cannot
# read refuses the whole index, so the same rule is checked here, before anything is published.
PATH_MAX = 256
# The root github.com chains to, by the name nife's images carry it under (`location-root` in
# nife's package_index::BASALT_SOURCES). A Release download starts there.
RELEASE_ROOT = "sectigo-root-e46"


def listed_location(release, stem):
    url = f"{release.rstrip('/')}/{stem}.nifepkg"
    if not url.startswith("https://"):
        fail(f"--release must be an https:// URL; nife's jig refuses anything else ({url})")
    path = "/" + url[len("https://"):].partition("/")[2]
    if len(path) > PATH_MAX or not all(0x21 <= ord(c) <= 0x7E for c in path) or "#" in path:
        fail(f"{url}: its path is not one nife's jig reads (printable ASCII, at most {PATH_MAX} bytes)")
    return f"{RELEASE_ROOT}={url}"


def publish(repository, site, release):
    index = repository / CHANNEL / "metadata" / "stand-in-index"
    targets = repository / CHANNEL / "targets"
    if not index.is_file():
        fail(f"{index} does not exist; run `build` first")
    out_metadata = site / CHANNEL / "metadata"
    out_targets = site / CHANNEL / "targets"
    out_metadata.mkdir(parents=True, exist_ok=True)
    out_targets.mkdir(parents=True, exist_ok=True)
    lines = []
    for line in index.read_text().splitlines():
        if not line:
            continue
        words = line.split(" ")
        if len(words) != 2 or not words[1].startswith("sha256:"):
            fail(f"{index}: {line!r} is not `<stem> sha256:<hex>`; was it published already?")
        stem, digest = words
        package = targets / f"{stem}.nifepkg"
        if not package.is_file() or f"sha256:{sha256(package)}" != digest:
            fail(f"{package} is missing or does not hash to the index's {digest}")
        shutil.copyfile(package, out_targets / package.name)
        lines.append(f"{stem} {digest} {listed_location(release, stem)}\n")
    if not lines:
        fail(f"{index} lists no packages; nothing to publish")
    (out_metadata / "stand-in-index").write_text("".join(lines))
    print(f"packages: {len(lines)} packages published into {site}")
    print("".join(lines), end="")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    b = sub.add_parser("build")
    b.add_argument("nife", type=pathlib.Path)
    b.add_argument("repository", type=pathlib.Path)
    b.add_argument("--triples", nargs="+", default=TRIPLES)
    p = sub.add_parser("publish")
    p.add_argument("repository", type=pathlib.Path)
    p.add_argument("site", type=pathlib.Path)
    p.add_argument("--release", required=True, help="the Release's download URL prefix")
    args = parser.parse_args()
    if args.verb == "build":
        build(args.nife, args.repository.resolve(), args.triples)
    else:
        publish(args.repository.resolve(), args.site.resolve(), args.release)


if __name__ == "__main__":
    main()
