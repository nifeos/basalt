#!/usr/bin/env python3
"""Build basalt's packages against a nife checkout, and lay them out as a repository.

Usage:
    helpers/packages.py build <nife-checkout> <repository-dir> [--triples T ...]
    helpers/packages.py publish <repository-dir> <site-dir> --release <url>
    helpers/packages.py check

For each recipe under recipes/, this fetches the pinned source, refuses it unless its SHA-256 is
the recipe's, builds the program for each nife target with that checkout's own toolchain, standard
library and manifest note, and packs it with that checkout's `cargo xtask package`, so the package
format has one producer. Then it writes the repository the way nife's index client reads it (nife
milestone 801 (packages over the internet), calef's Q4 on nife#1884):

    <repository-dir>/rolling/metadata/stand-in-index   one `<stem> sha256:<hex>` line per package
    <repository-dir>/rolling/targets/<stem>.nifepkg     the packages
    <repository-dir>/rolling/sources/<name>-<version>-source.tar.gz
                                                        what each package was built from

The source archive is the package's corresponding source, published beside every copy of the
package: the exact upstream files the recipe fetched (each checked against its SHA-256), the patches
it applied, the recipe itself, this script and pins.toml, and a `BUILT-FROM` note naming the nife
commit whose toolchain, standard library and C library the package was built and linked with. It is
written byte-for-byte reproducibly (sorted members, fixed times and owners), so a rerun of the same
commit makes the same archive. ioping is GPL-3.0-or-later, so its binaries may only be offered with
this; every package gets one so no recipe has to remember. The archive's name and layout are
provisional.

The index is nife's stand-in encoding until its TUF client exists (nife milestone 858). The channel
name `rolling` is provisional, as nife's `package_index::PROVISIONAL_CHANNEL` is.

`publish` turns a built repository into what is served (calef, 2026-10-12 UTC: "Packages on
Releases, index on Pages"). It writes <site-dir>, the GitHub Pages site behind basalt.nifeos.org,
with the index's every line given one listed location, the package's asset on the GitHub Release
whose download URL prefix is --release:

    <site-dir>/rolling/metadata/stand-in-index   `<stem> sha256:<hex> sectigo-root-e46=<release>/<stem>.nifepkg`
    <site-dir>/rolling/targets/<stem>.nifepkg     basalt's own copy, which nife's jig tries last
    <site-dir>/rolling/sources/<name>-<version>-source.tar.gz   the source of that copy

The listed location names the root its certificate chains to, `sectigo-root-e46`, which is
github.com's: calef's C2, ruled 2026-10-12 UTC on nife#2048 (nife section 250, clauses 11 to 13).
jig fetches it under that root and follows its one redirect to an ISRG Root X1 host.

The copy under targets/ is nife's section 250, clause 9 (2026-10-10 amendment): "basalt always
keeps a copy under its own targets/, which is tried last". It is the fallback when the Release
cannot be reached; calef has not ruled on dropping it.

Every recipe names its `license`, an SPDX expression, and its `license_files`, paths inside the
unpacked upstream source. `check` refuses a recipe missing either, and `build` runs `check` before
it fetches anything. Each package carries those files as members `doc/<file name>`, and a crate
recipe also carries `doc/THIRD-PARTY-NOTICES`: the license files of every crate it is built from,
each distinct text once with the crates that ship it. The `doc/` prefix is nife milestone 918's
(a package carries its program's documentation); the license member names and the notices file
are provisional until 918 lands its member convention.

    helpers/packages.py check

Every recipe records its packages' digests in a `[package_digest]` table, one SHA-256 per
architecture, and `build` fails unless each package hashes to its line; `build --update-digests`
rewrites the table instead (nife milestone 917, basalt's packages build reproducibly).

Three recipe shapes are understood, each with one program. A recipe with a `[nife]` table is a
first-party program, a `[[bin]]` of the pinned nife's own workspace (rmle). A recipe naming a
`crate` is a Rust crate with its patched dependencies (ripgrep). A recipe with `[[source]]` and `[[object]]` tables is C
compiled against the pinned nife's C library (ioping, hackbench and SQLite, nife milestones 906, 907
and 897); a source with `unpack = true` is a zip extracted beside it, no member outside it.

Name and interface provisional.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile

BASALT = pathlib.Path(__file__).resolve().parent.parent
USER_AGENT = "basalt-packages (https://github.com/nifeos/basalt)"
TRIPLES = ["aarch64-unknown-nife", "riscv64-unknown-nife", "x86_64-unknown-nife"]
CHANNEL = "rolling"


def fail(message):
    print(f"packages: {message}", file=sys.stderr)
    sys.exit(1)


# An SPDX license expression, checked for shape: identifiers (`LicenseRef-` ones included, with an
# optional `+`), `AND`, `OR`, `WITH` and balanced parentheses. Whether an identifier is on the SPDX
# list is not checked here; review is.
SPDX_ID = re.compile(r"(LicenseRef-)?[A-Za-z0-9][A-Za-z0-9.-]*\+?")


def spdx_ok(expression):
    """Whether `expression` parses as `term ((AND|OR) term)*`, where a term is `( expression )` or
    an identifier with an optional `WITH <exception>`."""
    tokens = expression.replace("(", " ( ").replace(")", " ) ").split()
    at = 0

    def identifier():
        nonlocal at
        if at < len(tokens) and tokens[at] not in ("AND", "OR", "WITH") and SPDX_ID.fullmatch(tokens[at]):
            at += 1
            return True
        return False

    def term():
        nonlocal at
        if at < len(tokens) and tokens[at] == "(":
            at += 1
            if not expr() or at >= len(tokens) or tokens[at] != ")":
                return False
            at += 1
            return True
        if not identifier():
            return False
        if at < len(tokens) and tokens[at] == "WITH":
            at += 1
            return identifier()
        return True

    def expr():
        nonlocal at
        if not term():
            return False
        while at < len(tokens) and tokens[at] in ("AND", "OR"):
            at += 1
            if not term():
                return False
        return True

    return expr() and at == len(tokens)


# A member name is at most nife's package_archive::NAME_LEN bytes.
NAME_LEN = 32
LICENSE_PREFIX = "doc/"
NOTICES = "THIRD-PARTY-NOTICES"


# The recipe table that records each package's digest, one `<architecture> = "sha256:<hex>"` line per
# nife architecture. The build refuses a package whose bytes hash to anything else (nife milestone
# 917). The table is the last in the file, so `rewrite_digests` can replace it without parsing TOML.
DIGEST_TABLE = "package_digest"
ARCHITECTURES = [t.split("-")[0] for t in TRIPLES]
DIGEST_NOTE = (
    "# Each package's SHA-256, checked by every build; `helpers/packages.py build <nife> <dir>\n"
    "# --update-digests` rewrites this table after a reviewed change.\n"
)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def check_recipe(path, digests_required=True):
    """The recipe's problems, as messages; empty when it may be built."""
    recipe = tomllib.loads(path.read_text())
    problems = []
    recorded = recipe.get(DIGEST_TABLE)
    if recorded is None:
        if digests_required:
            problems.append(f"has no [{DIGEST_TABLE}] table (run `helpers/packages.py build --update-digests`)")
    elif not isinstance(recorded, dict) or sorted(recorded) != sorted(ARCHITECTURES) or not all(
        isinstance(v, str) and DIGEST.fullmatch(v) for v in recorded.values()
    ):
        problems.append(f"[{DIGEST_TABLE}] must hold one `sha256:<64 hex digits>` per architecture: "
                        + ", ".join(ARCHITECTURES))
    license = recipe.get("license")
    if not isinstance(license, str) or not license.strip():
        problems.append("names no `license` (an SPDX expression)")
    elif not spdx_ok(license):
        problems.append(f"`license = {license!r}` is not an SPDX expression")
    files = recipe.get("license_files")
    if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
        problems.append("names no `license_files` (paths inside the upstream source)")
    else:
        for f in files:
            member = LICENSE_PREFIX + pathlib.PurePosixPath(f).name
            if len(member.encode()) > NAME_LEN:
                problems.append(f"license file {f!r} makes member {member!r}, over {NAME_LEN} bytes")
        names = [pathlib.PurePosixPath(f).name for f in files]
        if len(set(names)) != len(names) or NOTICES in names:
            problems.append("two license files share a name, or one is named " + NOTICES)
    return [f"{path.relative_to(BASALT)}: {p}" for p in problems]


def check(digests_required=True):
    problems = []
    for path in sorted((BASALT / "recipes").glob("*.toml")):
        problems += check_recipe(path, digests_required)
    for p in problems:
        print(f"packages: {p}", file=sys.stderr)
    if problems:
        fail(f"{len(problems)} recipe problem(s); see CONTRIBUTING.md")
    print("packages: every recipe names its license and license files, and its packages' digests")


def license_docs(recipe, src):
    """[(member name, path)] for the recipe's license files, refused if one is missing."""
    docs = []
    for f in recipe["license_files"]:
        path = src / f
        if not path.is_file():
            fail(f"{recipe['name']}: license file {f} is not in the upstream source")
        docs.append((LICENSE_PREFIX + pathlib.PurePosixPath(f).name, path))
    return docs


LICENSE_NAME = re.compile(r"(LICEN[CS]E|COPYING|UNLICENSE|NOTICE|COPYRIGHT)", re.IGNORECASE)


def third_party_notices(recipe, src, config, dest):
    """Write `dest`: the license files of every crate the recipe's crate depends on, each distinct
    text once, headed by the crates that ship it.

    The crates are those reached from the root through normal dependencies with no platform
    condition, or one naming nife. Every condition in ripgrep's graph (unix, windows, msvc, musl
    and named Windows triples) is false on nife's targets, so those crates are not built. Build-time
    crates (proc macros) are listed, a superset of what the program contains."""
    meta = json.loads(
        subprocess.run(
            ["cargo", "metadata", "--format-version", "1", "--locked", *config],
            cwd=src, check=True, capture_output=True, text=True,
        ).stdout
    )
    packages = {p["id"]: p for p in meta["packages"]}
    nodes = {n["id"]: n for n in meta["resolve"]["nodes"]}
    root = meta["resolve"]["root"]
    seen, stack = set(), [root]
    while stack:
        for dep in nodes[stack.pop()]["deps"]:
            if dep["pkg"] in seen:
                continue
            if any(k["kind"] is None and (k["target"] is None or "nife" in k["target"])
                   for k in dep["dep_kinds"]):
                seen.add(dep["pkg"])
                stack.append(dep["pkg"])
    texts = {}
    for pid in seen:
        package = packages[pid]
        crate = f"{package['name']} {package['version']} ({package.get('license') or 'no license field'})"
        directory = pathlib.Path(package["manifest_path"]).parent
        found = sorted(f for f in directory.iterdir() if f.is_file() and LICENSE_NAME.match(f.name))
        if not found:
            texts.setdefault(b"(no license file shipped in the crate)", []).append(crate)
        for f in found:
            texts.setdefault(f.read_bytes(), []).append(f"{crate}: {f.name}")
    out = [
        f"Third-party notices for {recipe['name']} {recipe['version']} as basalt builds it.\n\n"
        f"The license files of the {len(seen)} crates it is built from, each distinct text once,\n"
        "after the crates and files that carry it. The program's own license files are beside\n"
        "this one.\n".encode()
    ]
    for text, holders in sorted(texts.items(), key=lambda kv: sorted(kv[1])):
        out.append(b"\n" + b"=" * 78 + b"\n")
        out += [f"{h}\n".encode() for h in sorted(holders)]
        out.append(b"=" * 78 + b"\n\n" + text.rstrip(b"\n") + b"\n")
    dest.write_bytes(b"".join(out))
    return dest


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def fetch(url, path, want):
    """`url` into `path`, refused unless it hashes to `want`. A copy already there is kept if it does."""
    if not path.exists() or sha256(path) != want:
        print(f"packages: fetching {url}")
        # A named User-Agent: kernel.org refuses Python's default one with 403. The digest, not
        # the transport, decides whether the bytes are taken.
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as r:
            path.write_bytes(r.read())
    got = sha256(path)
    if got != want:
        path.unlink()
        fail(f"{url} has SHA-256 {got}, not the recipe's {want}; refusing it")
    return path


def fetch_crate(crate, version, want, work):
    """The .crate file, from crates.io, refused unless it hashes to `want`."""
    url = f"https://static.crates.io/crates/{crate}/{crate}-{version}.crate"
    return fetch(url, work / f"{crate}-{version}.crate", want)


def unpack(crate_file, work):
    with tarfile.open(crate_file) as t:
        t.extractall(work, filter="data")


def run(cmd, cwd, env=None):
    print("packages: $ " + " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, check=True)


# Where a build happens, whatever checkout it was asked to build and wherever that sits.
#
# Cargo hashes a path dependency that lies outside its workspace root into the crate ids by absolute
# path, and nife's `c_library` is a workspace of its own with dependencies in `../crates`. The ids
# set the order of the program's functions, so building from a checkout at another path gave
# another program (measured on nife milestone 917). Trimming paths does not reach that hash, so the
# build runs at one fixed path instead: nife is staged here from its commit, and so is the
# recipes' scratch space. `/tmp` is a symbolic link on macOS, where the physical path differs, so
# a package rebuilt there is not expected to match one built on Linux.
BUILD_ROOT = pathlib.Path("/tmp/basalt-build")


def stage_nife(checkout):
    """The tree of `checkout`'s HEAD, exported to BUILD_ROOT/nife, and that path."""
    staged = BUILD_ROOT / "nife"
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    staged.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "archive", "HEAD"], cwd=checkout, check=True, capture_output=True
    ).stdout
    subprocess.run(["tar", "-x", "-C", staged], input=archive, check=True)
    return staged


# Cargo remaps the workspace, the registry and the toolchain's sources itself, and keeps the
# rewrite out of the flags it hashes. A `--remap-path-prefix` in RUSTFLAGS would carry this
# machine's paths into every crate's id (measured on nife milestone 917: the ids of `c_library` and
# `c_program` moved with the checkout path, and with them the order of the program's functions).
TRIM_PATHS = {"CARGO_UNSTABLE_TRIM_PATHS": "true", "CARGO_PROFILE_RELEASE_TRIM_PATHS": "all"}


def build_ripgrep_shape(recipe, nife, work, triples):
    """The crate, its one patched dependency, built for each triple.

    Returns ({triple: elf path}, [each fetched source file], [(doc member, path)])."""
    name, version = recipe["crate"], recipe["version"]
    crate_file = fetch_crate(name, version, recipe["sha256"], work)
    sources = [crate_file]
    unpack(crate_file, work)
    src = work / f"{name}-{version}"
    config = []
    for patch in recipe.get("patch", []):
        # Inside the crate's own directory, not beside it: cargo hashes a path dependency into the
        # crate ids (and so into the program: type ids, say) by its absolute path unless it is under
        # the workspace root, where it hashes the relative one. Beside the crate, the build's
        # temporary directory would be in the program's bytes.
        patched = src / "patched"
        dep = patched / f"{patch['crate']}-{patch['version']}"
        patch_file = BASALT / patch["file"]
        if sha256(patch_file) != patch["file_sha256"]:
            fail(
                f"{patch['file']} has SHA-256 {sha256(patch_file)}, not the recipe's "
                f"{patch['file_sha256']}: the patch changed; review it and the recipe together"
            )
        shutil.rmtree(dep, ignore_errors=True)
        dep_file = fetch_crate(patch["crate"], patch["version"], patch["sha256"], work)
        sources.append(dep_file)
        patched.mkdir(exist_ok=True)
        unpack(dep_file, patched)
        run(["patch", "-p1", "-s", "-i", patch_file], cwd=dep)
        config += ["--config", f'patch.crates-io.{patch["crate"]}.path="{dep}"']
        # The lock file names the registry copy; a patched path dependency carries no source or
        # checksum, so those two lines go from its entry and nothing else in the lock moves.
        lock = src / "Cargo.lock"
        kept, inside = [], False
        for line in lock.read_text().splitlines():
            if line == f'name = "{patch["crate"]}"':
                inside = True
            elif line == "":
                inside = False
            if inside and line.startswith(("source = ", "checksum = ")):
                continue
            kept.append(line)
        lock.write_text("\n".join(kept) + "\n")
    docs = license_docs(recipe, src)
    notices = work / f"{name}-{version}.{NOTICES}"
    docs.append((LICENSE_PREFIX + NOTICES, third_party_notices(recipe, src, config, notices)))
    run(["cargo", "xtask", "std-src"], cwd=nife)
    out = {}
    for triple in triples:
        dest = nife / "target" / "basalt" / recipe["name"] / triple
        dest.mkdir(parents=True, exist_ok=True)
        note = dest / "note.o"
        run(["cargo", "xtask", "foreign-note", triple, note], cwd=nife)
        # The link inputs sit beside the crate and are named relative to it (cargo runs rustc, and
        # rustc the linker, in the workspace root): an absolute path in RUSTFLAGS would be hashed
        # into the crates' ids, see TRIM_PATHS.
        shutil.copyfile(nife / "crates" / "user_mode_runtime" / "link.ld", src / "link.ld")
        shutil.copyfile(note, src / "note.o")
        env = dict(os.environ, **TRIM_PATHS)
        env.update(
            {
                # nife's release profile carries overflow checks (its notes/overflow-checks.md).
                "CARGO_PROFILE_RELEASE_OVERFLOW_CHECKS": "true",
                "RUSTUP_TOOLCHAIN": str(nife / "target" / "nife-farm"),
                # Symbols too, not only debug info, as nife strips every program it packs: a package
                # may be at most 4 MiB (nife's spawnproto::IMAGE_MAX_PAGES). The manifest note is a
                # PT_NOTE, which stripping keeps.
                "RUSTFLAGS": (
                    "-Clink-arg=-Tlink.ld -Clink-arg=-u_start -Clink-arg=--build-id=none "
                    "-Clink-arg=note.o -Cstrip=symbols -Copt-level=s"
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
    return out, sources, docs


def source_name(source):
    """The name a C recipe's source is saved under: its `file`, or else the URL's last component.
    A URL that ends in a query (kernel.org's cgit names the tag with `?h=`) needs `file`."""
    name = source.get("file") or source["url"].rsplit("/", 1)[1]
    if "/" in name or "?" in name or name in ("", ".", ".."):
        fail(f"source {source['url']}: {name!r} is not a file name; give the source a `file`")
    return name


def unpack_zip(archive, into):
    """Extract a source's zip into `into`, refusing a member that would land outside it.

    The zip is already pinned by its SHA-256, so a hostile member would need a reviewed recipe
    to vouch for it; the check is the second lock. An absolute name, a `..` step, a drive letter
    and a symbolic link (a zip can carry one in its Unix mode bits) are each refused by name."""
    root = into.resolve()
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename
            target = (root / name).resolve()
            if (
                name.startswith(("/", "\\"))
                or ":" in name
                or ".." in pathlib.PurePosixPath(name).parts
                or not target.is_relative_to(root)
            ):
                fail(f"{archive.name}: member {name!r} would land outside {root}; refusing it")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                fail(f"{archive.name}: member {name!r} is a symbolic link; refusing it")
        z.extractall(root)


def build_c_shape(recipe, nife, work, triples):
    """C sources compiled against the pinned nife's C library, one object per `[[object]]`, and
    linked by that nife's `helpers/build-c-program.sh`.

    Returns ({triple: elf path}, [each fetched source file], [(doc member, path)]).

    Everything that makes a C program a nife program is the pinned commit's, so nothing here can
    drift from it: the compiler (`helpers/c-library-cc.sh`), the target and library flags and
    headers (`helpers/c-library-cflags.sh`), and the link with the standard library and the
    manifest note (`helpers/build-c-program.sh`, which runs `cargo xtask std-src` and `cargo xtask
    foreign-note`). The recipe adds only its own sources and flags."""
    src = work / recipe["name"]
    src.mkdir(parents=True, exist_ok=True)
    sources = [
        fetch(source["url"], src / source_name(source), source["sha256"])
        for source in recipe["source"]
    ]
    # A source with `unpack = true` is a zip (SQLite's amalgamation), extracted beside it after its
    # digest is checked; the recipe's objects then name files inside it.
    for source, path in zip(recipe["source"], sources):
        if source.get("unpack"):
            unpack_zip(path, src)
    docs = license_docs(recipe, src)
    cc = subprocess.run(
        [nife / "helpers" / "c-library-cc.sh"], cwd=nife, check=True, capture_output=True, text=True
    ).stdout.strip()
    sysroot = subprocess.run(
        ["rustc", "--print", "sysroot"], cwd=nife, check=True, capture_output=True, text=True
    ).stdout.strip()
    objcopy = next(pathlib.Path(sysroot, "lib", "rustlib").glob("*/bin/llvm-objcopy"))
    out = {}
    for triple in triples:
        flags = subprocess.run(
            [nife / "helpers" / "c-library-cflags.sh", triple],
            cwd=nife, check=True, capture_output=True, text=True,
        ).stdout.split()
        objects = []
        for obj in recipe["object"]:
            o = work / recipe["name"] / "obj" / triple / (pathlib.Path(obj["source"]).stem + ".o")
            o.parent.mkdir(parents=True, exist_ok=True)
            run([cc, *flags, *obj.get("cflags", []), "-c", src / obj["source"], "-o", o], cwd=src)
            objects.append(o)
        program = recipe["program"]
        # The link builds the Rust standard library and the C program's runtime.
        link_env = dict(os.environ, **TRIM_PATHS)
        run([nife / "helpers" / "build-c-program.sh", program, triple, *objects], cwd=nife,
            env=link_env)
        dest = nife / "target" / "basalt" / recipe["name"] / triple
        dest.mkdir(parents=True, exist_ok=True)
        elf = dest / program
        # Symbols too, as ripgrep's build strips them: a package may be at most 4 MiB (nife's
        # spawnproto::IMAGE_MAX_PAGES). The manifest note is an allocated PT_NOTE, which this keeps.
        run([objcopy, "--strip-all", nife / "target" / program / triple / program, elf], cwd=nife)
        out[triple] = elf
    return out, sources, docs


# nife's bare-metal target for each nife triple: a first-party program on nife's own runtime is
# built for these, as nife's archive builds it (its `archive::build_programs`).
NATIVE_TARGETS = {
    "aarch64-unknown-nife": "aarch64-unknown-none-softfloat",
    "riscv64-unknown-nife": "riscv64imac-unknown-none-elf",
    "x86_64-unknown-nife": "x86_64-unknown-none",
}


def build_nife_shape(recipe, nife, work, triples):
    """A first-party program: one `[[bin]]` of the pinned nife's own workspace, built at that commit
    for each bare-metal target, in its release profile, symbols stripped.

    Returns ({triple: elf path}, [], [(doc member, path)]). The source is the nife commit itself,
    which `BUILT-FROM` names, so nothing is fetched, and the license files are read from that
    checkout."""
    package, program = recipe["nife"]["package"], recipe["program"]
    # Not RUSTFLAGS: it would replace the per-target `rustflags` of nife's .cargo/config.toml
    # (x86_64-unknown-none carries some), which are part of how nife builds its own programs.
    # trim-paths rewrites the workspace, the registry and the toolchain's sources instead.
    env = dict(os.environ, **TRIM_PATHS)
    docs = license_docs(recipe, nife)
    sysroot = subprocess.run(
        ["rustc", "--print", "sysroot"], cwd=nife, check=True, capture_output=True, text=True
    ).stdout.strip()
    objcopy = next(pathlib.Path(sysroot, "lib", "rustlib").glob("*/bin/llvm-objcopy"))
    out = {}
    for triple in triples:
        target = NATIVE_TARGETS[triple]
        run(["cargo", "build", "--release", "--locked", "-p", package, "--bin", program,
             "--target", target], cwd=nife, env=env)
        dest = nife / "target" / "basalt" / recipe["name"] / triple
        dest.mkdir(parents=True, exist_ok=True)
        elf = dest / program
        run([objcopy, "--strip-all", nife / "target" / target / "release" / program, elf], cwd=nife)
        out[triple] = elf
    return out, [], docs


def pack(recipe, nife, triple, elf, docs):
    """One nife package through nife's own producer: the program, then each doc member. Returns
    its stem and path."""
    architecture = triple.split("-")[0]
    nife_recipe = elf.parent / f"{recipe['name']}-{architecture}.recipe.toml"
    # nife's producer reads a member's file by a path inside the nife checkout.
    members = [(f"bin/{recipe['program']}", elf)]
    for member, path in docs:
        copy = elf.parent / member
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, copy)
        members.append((member, copy))
    nife_recipe.write_text(
        f'name = "{recipe["name"]}"\nversion = "{recipe["version"]}"\n'
        f'architecture = "{architecture}"\n'
        + "".join(f'\n[[member]]\nname = "{m}"\nfile = "{f.relative_to(nife)}"\n' for m, f in members)
    )
    run(["cargo", "xtask", "package", nife_recipe], cwd=nife)
    stem = f"{recipe['name']}-{recipe['version']}-{architecture}"
    return stem, nife / "target" / "packages" / f"{stem}.nifepkg"


def source_archive(recipe, recipe_path, sources, nife, dest):
    """The package's corresponding source as one reproducible .tar.gz at `dest`.

    Members, under `<name>-<version>-source/`: `upstream/` (each fetched file, as fetched),
    `patches/` (each patch the recipe applies), `recipes/<name>.toml`, `helpers/packages.py`,
    `pins.toml` and `BUILT-FROM`."""
    top = f"{recipe['name']}-{recipe['version']}-source"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=nife, check=True, capture_output=True, text=True
    ).stdout.strip()
    pins = tomllib.loads((BASALT / "pins.toml").read_text())
    note = (
        f"{recipe['name']} {recipe['version']}, as basalt builds it.\n\n"
        f"Built and linked with the toolchain, standard library and C library of nife at\n"
        f"{pins['nife']['repository']} commit {commit}\n"
        f"(the nife pin in pins.toml is {pins['nife']['commit']}).\n\n"
        "upstream/ holds the files the recipe fetched, each checked against the SHA-256 the\n"
        "recipe names; patches/ holds what the recipe applies to them. helpers/packages.py is\n"
        "the build: `helpers/packages.py build <nife checkout> <repository-dir>`.\n"
    )
    if "nife" in recipe:
        note += (
            f"\nA first-party package: its source is that nife commit itself (the `[[bin]]`\n"
            f"{recipe['program']} of its `{recipe['nife']['package']}` package), so upstream/ is "
            "empty.\n"
        )
    members = [(f"upstream/{s.name}", s.read_bytes()) for s in sources]
    for patch in recipe.get("patch", []):
        members.append((patch["file"], (BASALT / patch["file"]).read_bytes()))
    members += [
        (f"recipes/{recipe_path.name}", recipe_path.read_bytes()),
        ("helpers/packages.py", (BASALT / "helpers" / "packages.py").read_bytes()),
        ("pins.toml", (BASALT / "pins.toml").read_bytes()),
        ("BUILT-FROM", note.encode()),
    ]
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.PAX_FORMAT) as t:
        for name, data in sorted(members):
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            t.addfile(info, io.BytesIO(data))
    with open(dest, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", filename="", mtime=0) as z:
        z.write(tar_bytes.getvalue())


def rewrite_digests(path, digests):
    """Replace (or append) the recipe's [package_digest] table with `digests`, {architecture: digest}.

    The table is the file's last, so everything from its header on is the old one."""
    text = path.read_text()
    head = text.split(f"\n[{DIGEST_TABLE}]")[0].split("\n\n" + DIGEST_NOTE)[0].rstrip("\n")
    table = "".join(f'{a} = "{digests[a]}"\n' for a in ARCHITECTURES)
    path.write_text(
        f"{head}\n\n{DIGEST_NOTE}[{DIGEST_TABLE}]\n{table}"
    )


def build(nife, repository, triples, only=(), update_digests=False):
    check(digests_required=not update_digests)
    checkout = nife.resolve()
    nife = stage_nife(checkout)
    targets = repository / CHANNEL / "targets"
    metadata = repository / CHANNEL / "metadata"
    source_dir = repository / CHANNEL / "sources"
    for d in (targets, metadata, source_dir):
        d.mkdir(parents=True, exist_ok=True)
    lines = []
    mismatches = []
    # Outside both checkouts: an unpacked crate with no [workspace] table finds the nearest
    # enclosing workspace and refuses to build inside it.
    work = BUILD_ROOT / "work"
    work.mkdir()
    for path in sorted((BASALT / "recipes").glob("*.toml")):
        if only and path.stem not in only:
            continue
        recipe = tomllib.loads(path.read_text())
        if "nife" in recipe:
            shape = build_nife_shape
        elif "object" in recipe:
            shape = build_c_shape
        else:
            shape = build_ripgrep_shape
        elfs, sources, docs = shape(recipe, nife, work, triples)
        built = {}
        for triple, elf in elfs.items():
            stem, package = pack(recipe, nife, triple, elf, docs)
            shutil.copyfile(package, targets / f"{stem}.nifepkg")
            digest = f"sha256:{sha256(package)}"
            lines.append(f"{stem} {digest}\n")
            built[triple.split("-")[0]] = digest
            want = recipe.get(DIGEST_TABLE, {}).get(triple.split("-")[0])
            if want != digest and not update_digests:
                mismatches.append(f"{stem}: built {digest}, {path.name} records {want}")
        if update_digests:
            if sorted(built) != sorted(ARCHITECTURES):
                fail("--update-digests needs every architecture built; drop --triples")
            rewrite_digests(path, built)
        archive = source_dir / f"{recipe['name']}-{recipe['version']}-source.tar.gz"
        source_archive(recipe, path, sources, checkout, archive)
        print(f"packages: {archive.name} sha256:{sha256(archive)}")
    (metadata / "stand-in-index").write_text("".join(lines))
    print(f"packages: {len(lines)} packages in {repository}")
    print("".join(lines), end="")
    if mismatches:
        for m in mismatches:
            print(f"packages: {m}", file=sys.stderr)
        fail(
            "a package's bytes are not the recipe's recorded digest: either the build is not "
            "reproducible here, or a reviewed change moved it (then `--update-digests` records "
            "the new ones, and the diff is the review)"
        )


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
    out_sources = site / CHANNEL / "sources"
    for d in (out_metadata, out_targets, out_sources):
        d.mkdir(parents=True, exist_ok=True)
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
    # Every package is published beside its source, here as on the Release.
    for stem in (line.split(" ")[0] for line in lines):
        name_version = stem.rsplit("-", 1)[0]
        archive = repository / CHANNEL / "sources" / f"{name_version}-source.tar.gz"
        if not archive.is_file():
            fail(f"{stem} has no source archive ({archive}); refusing to publish it without one")
        shutil.copyfile(archive, out_sources / archive.name)
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
    b.add_argument("--update-digests", action="store_true",
                   help="record the built packages' digests in their recipes instead of checking them")
    b.add_argument("--only", nargs="+", default=[], help="build only these recipes (by file stem)")
    p = sub.add_parser("publish")
    p.add_argument("repository", type=pathlib.Path)
    p.add_argument("site", type=pathlib.Path)
    p.add_argument("--release", required=True, help="the Release's download URL prefix")
    sub.add_parser("check", help="refuse a recipe with no license or license files")
    args = parser.parse_args()
    if args.verb == "check":
        check()
    elif args.verb == "build":
        build(args.nife, args.repository.resolve(), args.triples, args.only, args.update_digests)
    else:
        publish(args.repository.resolve(), args.site.resolve(), args.release)


if __name__ == "__main__":
    main()
