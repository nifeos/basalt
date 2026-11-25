#!/usr/bin/env python3
"""Propose a recipe's newest upstream release, and decide whether it may merge with no person.

This is the recipe bump of record §328 (a recipe may change with no person when checks stand in
for review), ruled option A on 2026-11-25 (UTC). The workflow `.github/workflows/recipe-bump.yml`
calls each verb in turn; each one does one step, so a step's log says which check said what.

Usage:
    helpers/recipe_bump.py watched                          JSON list of the watched recipes
    helpers/recipe_bump.py plan <recipe> [--version V]      KEY=VALUE lines: what to propose
    helpers/recipe_bump.py rewrite <recipe> <version> <sha256> <work>
    helpers/recipe_bump.py check <recipe> <work> <results.json>
    helpers/recipe_bump.py served <recipe> <version> <arch> <dest>   what basalt serves today
    helpers/recipe_bump.py note-tool <nife-checkout>        build the note comparison, print its path
    helpers/recipe_bump.py note <results.json> <exit> <output-file>
    helpers/recipe_bump.py confined <recipe> <base> <head> <results.json>
    helpers/recipe_bump.py decide <results.json> <eligible-at>

**Which recipes are watched.** A recipe naming a `crate` is watched through crates.io: the newest
release comes from crates.io's index, and the `.crate`'s SHA-256 is that index's `cksum`, which
§328 requires (the upstream's own index, not a download hashed here). Every other recipe is listed
in NOT_WATCHED with its reason. A recipe opts out the same way, by a line there with its reason.

**Which release is proposed.** The newest release that is not yanked, not a pre-release, newer than
the recipe's, and past its source's hold (HOLD_DAYS). A newer release still inside the hold is
reported and waits: the next scheduled run proposes it once it has aged, replacing the open pull
request, as §328's clause 1 says a newer release does. Proposing it at once would replace a pull
request that could merge with one that cannot for days. `--version` asks for one release instead;
the hold still applies to it, through `decide`.

**The patched dependency.** When the new release's `Cargo.lock` takes another version of a crate
the recipe patches, `rewrite` moves that `[[patch]]` entry's `version` and `sha256`, the second also
read from crates.io's index and checked against the lockfile's checksum, and leaves `file` and
`file_sha256` alone. `check` then dry-runs the unchanged patch against the new version.

**The decision.** `decide` reads the results the other verbs wrote and prints `outcome=arm`, `hold`
or `wait`, with one `reason=` line per reason. `hold` means a person must look: the endowment widened
or could not be compared, the license changed, a patch no longer applies, or the diff is not a
recipe bump. `wait` means the release is younger than the hold and nothing else is wrong. Anything
else that fails (the build, the reproduce, install and composed jobs) is a red pull request, as a
red pin bump is, and needs no label.

Names provisional: this file, its verbs, the results keys, HOLD_DAYS and the branch `recipe/<name>`.
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tarfile
import tomllib
import urllib.request

import packages  # beside this file, which Python puts first on the path

BASALT = pathlib.Path(__file__).resolve().parent.parent

USER_AGENT = "basalt-recipe-bump (https://github.com/nifeos/basalt)"

# Days a release must age before its pull request may merge, per source (§328, clause 6). basalt's
# value is 7, provisional: the maintainer sets and changes it, as a two-way door.
HOLD_DAYS = {"crates.io": 7}

# Recipes the bump does not watch, and why. §328 has the bot read a release's SHA-256 from the
# upstream's own index; these upstreams publish none, so the bot would have to hash a download
# itself, which is the thing the rule replaces.
NOT_WATCHED = {
    "base": "first-party: the pin bump moves it with nife",
    "demos": "first-party: the pin bump moves it with nife",
    "hackbench": (
        "a single C file vendored from rt-tests on kernel.org, which publishes no release feed "
        "carrying a SHA-256"
    ),
    "ioping": (
        "single C files at a GitHub tag; a tag list carries no SHA-256 of the files, and a tag "
        "can move"
    ),
    "sqlite": (
        "the amalgamation zip; sqlite.org's download page lists SHA3-256 only, in HTML, not a "
        "machine-readable feed with a SHA-256"
    ),
}

# The label that asks a person, added to a held pull request. basalt has no `needs-architect`
# check, so it is `needs-maintainer`. Provisional.
LABEL = "needs-maintainer"

# The served index: the packages basalt publishes today, and where each one is.
SERVED_INDEX = "https://basalt.nifeos.org/rolling/metadata/stand-in-index"

NOTE_TOOL = BASALT / "tools" / "manifest_note_compare"


def fail(message):
    print(f"recipe_bump: {message}", file=sys.stderr)
    sys.exit(1)


def recipe_path(name):
    path = BASALT / "recipes" / f"{name}.toml"
    if not path.is_file():
        fail(f"no recipe {path.relative_to(BASALT)}")
    return path


def load(name):
    return tomllib.loads(recipe_path(name).read_text())


def source_of(recipe):
    """The release source a recipe is watched through, or None."""
    if recipe["name"] in NOT_WATCHED:
        return None
    if "crate" in recipe:
        return "crates.io"
    return None


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as r:
        return r.read()


# --- crates.io -----------------------------------------------------------------------------------


def index_path(crate):
    """Where crates.io's sparse index keeps a crate's file (the registry index layout)."""
    name = crate.lower()
    if len(name) <= 2:
        return f"{len(name)}/{name}"
    if len(name) == 3:
        return f"3/{name[0]}/{name}"
    return f"{name[:2]}/{name[2:4]}/{name}"


def index_entries(crate):
    """{version: {"cksum": hex, "yanked": bool}} from crates.io's index."""
    text = get(f"https://index.crates.io/{index_path(crate)}").decode()
    out = {}
    for line in text.splitlines():
        if line.strip():
            entry = json.loads(line)
            out[entry["vers"]] = {"cksum": entry["cksum"], "yanked": entry["yanked"]}
    return out


def published_dates(crate):
    """{version: when crates.io published it}. The index carries no date; the API does."""
    out = {}
    url = f"https://crates.io/api/v1/crates/{crate}/versions?per_page=100"
    while url:
        page = json.loads(get(url))
        for v in page["versions"]:
            out[v["num"]] = parse_time(v["created_at"])
        following = page.get("meta", {}).get("next_page")
        url = f"https://crates.io/api/v1/crates/{crate}/versions{following}" if following else None
    return out


def parse_time(text):
    return datetime.datetime.fromisoformat(text)


RELEASE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def release_key(version):
    """A release's order, or None for a pre-release or anything not plain `x.y.z`."""
    match = RELEASE.fullmatch(version)
    return tuple(int(n) for n in match.groups()) if match else None


def choose(current, entries, dates, now, hold_days, wanted=None):
    """What to propose: a dict with `version`, `sha256`, `published`, `eligible_at` and, when a
    newer release waits in the hold, `waiting`; or None when nothing newer exists."""
    floor = release_key(current)
    newer = sorted(
        (
            v
            for v, e in entries.items()
            if not e["yanked"] and release_key(v) and floor and release_key(v) > floor
        ),
        key=release_key,
    )
    hold = datetime.timedelta(days=hold_days)
    if wanted is not None:
        if wanted not in entries or entries[wanted]["yanked"]:
            fail(f"{wanted} is not a release crates.io's index lists, or it is yanked")
        candidates = [wanted]
    else:
        candidates = [v for v in newer if dates[v] + hold <= now] or newer[-1:]
    if not candidates:
        return None
    version = candidates[-1]
    chosen = {
        "version": version,
        "sha256": entries[version]["cksum"],
        "published": dates[version].isoformat(),
        "eligible_at": (dates[version] + hold).isoformat(),
    }
    younger = [v for v in newer if release_key(v) > release_key(version)]
    if younger:
        chosen["waiting"] = f"{younger[-1]} (eligible {(dates[younger[-1]] + hold).isoformat()})"
    return chosen


def plan(name, wanted, now=None):
    recipe = load(name)
    source = source_of(recipe)
    if source is None:
        fail(f"{name} is not watched: {NOT_WATCHED.get(name, 'it names no crate')}")
    now = now or datetime.datetime.now(datetime.UTC)
    entries = index_entries(recipe["crate"])
    chosen = choose(
        recipe["version"], entries, published_dates(recipe["crate"]), now, HOLD_DAYS[source], wanted
    )
    print(f"current={recipe['version']}")
    if chosen is None:
        print("skip=true")
        print(f"recipe_bump: {name} {recipe['version']} is crates.io's newest release", file=sys.stderr)
        return
    print("skip=false")
    for key in ("version", "sha256", "published", "eligible_at", "waiting"):
        if key in chosen:
            print(f"{key}={chosen[key]}")


# --- rewriting the recipe ------------------------------------------------------------------------

TABLE = re.compile(r"\[\[?([^\[\]]+)\]\]?")
KEY_LINE = re.compile(r'(version|sha256) = "[^"]*"')


def sections(text):
    """[(header, [lines])], where header is None before the first table and `[[patch]]` is a
    header too (`[[patch]]` once per entry)."""
    out = [(None, [])]
    for line in text.splitlines():
        if TABLE.fullmatch(line.strip()):
            out.append((line.strip(), []))
        else:
            out[-1][1].append(line)
    return out


def set_key(lines, key, value, where):
    hits = [i for i, line in enumerate(lines) if re.fullmatch(rf'{key} = "[^"]*"', line)]
    if len(hits) != 1:
        fail(f"expected one `{key} = \"...\"` line in {where}, found {len(hits)}")
    lines[hits[0]] = f'{key} = "{value}"'


def rewrite_text(text, version, sha256, patches):
    """The recipe's text with the new upstream `version` and `sha256`, and each patched crate's
    {crate: (version, sha256)} moved; every other line as it was."""
    parts = sections(text)
    set_key(parts[0][1], "version", version, "the recipe's top")
    set_key(parts[0][1], "sha256", sha256, "the recipe's top")
    for header, lines in parts[1:]:
        if header != "[[patch]]":
            continue
        crate = next(line for line in lines if line.startswith("crate = "))
        crate = crate.split('"')[1]
        if crate in patches:
            set_key(lines, "version", patches[crate][0], f"the [[patch]] for {crate}")
            set_key(lines, "sha256", patches[crate][1], f"the [[patch]] for {crate}")
    out = []
    for header, lines in parts:
        if header is not None:
            out.append(header)
        out.extend(lines)
    return "\n".join(out) + "\n"


def locked(lock_text, crate):
    """The one (version, checksum) a Cargo.lock takes for `crate`, from crates.io."""
    lock = tomllib.loads(lock_text)
    hits = [p for p in lock.get("package", []) if p["name"] == crate]
    if len(hits) != 1:
        fail(f"the new Cargo.lock takes {len(hits)} versions of {crate}, not one")
    if not hits[0].get("source", "").startswith("registry+") or "checksum" not in hits[0]:
        fail(f"the new Cargo.lock takes {crate} from somewhere other than crates.io")
    return hits[0]["version"], hits[0]["checksum"]


def unpacked(recipe, version, sha256, work):
    """The new release, fetched (refused unless it hashes to `sha256`) and unpacked in `work`."""
    work.mkdir(parents=True, exist_ok=True)
    crate_file = packages.fetch_crate(recipe["crate"], version, sha256, work)
    with tarfile.open(crate_file) as t:
        t.extractall(work, filter="data")
    return work / f"{recipe['crate']}-{version}"


def rewrite(name, version, sha256, work):
    path = recipe_path(name)
    recipe = tomllib.loads(path.read_text())
    src = unpacked(recipe, version, sha256, work)
    lock = src / "Cargo.lock"
    if not lock.is_file():
        fail(f"{recipe['crate']} {version} ships no Cargo.lock, so its dependencies are not fixed")
    moved = {}
    for patch in recipe.get("patch", []):
        dep_version, checksum = locked(lock.read_text(), patch["crate"])
        cksum = index_entries(patch["crate"]).get(dep_version, {}).get("cksum")
        if cksum != checksum:
            fail(
                f"{patch['crate']} {dep_version}: the lockfile's checksum {checksum} is not "
                f"crates.io's index cksum {cksum}"
            )
        moved[patch["crate"]] = (dep_version, cksum)
        print(f"recipe_bump: {patch['crate']} {patch['version']} -> {dep_version}")
    path.write_text(rewrite_text(path.read_text(), version, sha256, moved))
    print(f"recipe_bump: {name} {recipe['version']} -> {version}")


# --- the checks ----------------------------------------------------------------------------------


def patch_result(patch, dep_dir):
    """Whether a [[patch]] entry's file is the reviewed one and applies to `dep_dir`."""
    patch_file = BASALT / patch["file"]
    if packages.sha256(patch_file) != patch["file_sha256"]:
        return {"ok": False, "why": f"{patch['file']} is not the file the recipe reviewed"}
    done = subprocess.run(
        ["patch", "-p1", "--dry-run", "--batch", "-s", "-i", str(patch_file)],
        cwd=dep_dir,
        check=False,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        detail = (done.stdout + done.stderr).strip().splitlines()[:3]
        return {
            "ok": False,
            "why": f"{patch['file']} does not apply to {patch['crate']} {patch['version']}: "
            + " / ".join(detail),
        }
    return {"ok": True, "why": f"{patch['file']} applies to {patch['crate']} {patch['version']}"}


def license_result(recipe, src):
    """Whether the new source declares the recipe's license and ships its license files."""
    manifest = tomllib.loads((src / "Cargo.toml").read_text()).get("package", {})
    declared = manifest.get("license")
    reasons = []
    if declared != recipe["license"]:
        reasons.append(f"upstream declares license {declared!r}, the recipe says {recipe['license']!r}")
    for f in recipe["license_files"]:
        if not (src / f).is_file():
            reasons.append(f"license file {f} is not in the new source")
    if reasons:
        return {"ok": False, "why": "; ".join(reasons)}
    return {"ok": True, "why": f"upstream declares {declared}, and every license file is there"}


def check(name, work, results_file):
    """The patch and license checks against the source `rewrite` unpacked, into the results."""
    recipe = load(name)
    src = work / f"{recipe['crate']}-{recipe['version']}"
    results = {"license": license_result(recipe, src), "patch": {"ok": True, "why": "no patches"}}
    for patch in recipe.get("patch", []):
        dep = packages.fetch_crate(patch["crate"], patch["version"], patch["sha256"], work)
        with tarfile.open(dep) as t:
            t.extractall(work / "patched", filter="data")
        result = patch_result(patch, work / "patched" / f"{patch['crate']}-{patch['version']}")
        if not result["ok"] or results["patch"]["why"] == "no patches":
            results["patch"] = result
    save(results_file, results)
    for key, value in results.items():
        print(f"recipe_bump: {key}: {'passes' if value['ok'] else 'holds'}: {value['why']}")


def served(name, version, arch, dest):
    """The package basalt serves today for this recipe at `version` (the one being replaced) and
    architecture, refused unless it hashes to the served index's digest."""
    stem = f"{load(name)['name']}-{version}-{arch}"
    for line in get(SERVED_INDEX).decode().splitlines():
        words = line.split(" ")
        if words[0] == stem and len(words) == 3:
            url = words[2].split("=", 1)[1]
            packages.fetch(url, pathlib.Path(dest), words[1].removeprefix("sha256:"))
            print(f"recipe_bump: {stem} is served from {url}")
            return
    fail(f"the served index lists no {stem}")


def note_tool(nife):
    """Build the note comparison against `nife`'s crates and print the program's path.

    The tool names `grant_contract` at the revision nife's capability_manifest_note names, so the
    two agree on the Manifest type; that revision moves with the nife pin, so it is copied from the
    checkout here rather than kept in step by hand."""
    want = re.search(
        r"^grant_contract = .*$",
        (pathlib.Path(nife) / "crates/capability_manifest_note/Cargo.toml").read_text(),
        re.MULTILINE,
    )
    if want is None:
        fail("nife's capability_manifest_note names no grant_contract dependency")
    manifest = NOTE_TOOL / "Cargo.toml"
    text = manifest.read_text()
    text = re.sub(r"^grant_contract = .*$", lambda _: want.group(0), text, flags=re.MULTILINE)
    manifest.write_text(text)
    target = NOTE_TOOL / "target"
    subprocess.run(
        ["cargo", "build", "--release", "--manifest-path", manifest, "--target-dir", target],
        check=True,
    )
    print(target / "release" / "manifest_note_compare")


def note_result(exit_code, output):
    """The note comparison's verdict, from the tool's exit and its lines."""
    lines = [line for line in output.splitlines() if line.strip()]
    if exit_code == 0 and lines:
        return {"ok": True, "why": "; ".join(lines)}
    if exit_code == 1:
        return {"ok": False, "why": "the endowment may widen: " + "; ".join(lines)}
    return {"ok": False, "why": "the notes could not be compared: " + ("; ".join(lines) or "no output")}


def note(results_file, exit_code, output_file):
    results = load_results(results_file)
    results["note"] = note_result(int(exit_code), pathlib.Path(output_file).read_text())
    save(results_file, results)
    print(f"recipe_bump: note: {'passes' if results['note']['ok'] else 'holds'}: {results['note']['why']}")


# --- the diff guard ------------------------------------------------------------------------------

DIGEST_LINE = re.compile(r'[A-Za-z0-9_]+ = "sha256:[0-9a-f]{64}"')


def confined_refusals(name, changed, old_text, new_text):
    """Why a diff is not a bump of this recipe's version and digests, as messages; empty when it is.

    `changed` is [(status, path)]. Only `recipes/<name>.toml` may change, and in it only: the top's
    `version` and `sha256` lines, each `[[patch]]`'s `version` and `sha256` lines, and the
    `[package_digest]` digest lines. The parsed tables must then agree apart from those keys, so a
    header hidden in a string cannot pass the line test and change the meaning."""
    path = f"recipes/{name}.toml"
    refusals = [f"{p}: {s}, and only {path} may change" for s, p in changed if (s, p) != ("M", path)]
    if ("M", path) not in changed:
        return refusals + [f"{path} did not change"]
    old, new = sections(old_text), sections(new_text)
    if [h for h, _ in old] != [h for h, _ in new]:
        return refusals + [f"{path}: its tables were added, removed or reordered"]
    for (header, a), (_, b) in zip(old, new):
        if header == "[package_digest]":
            keep = [line for line in a if not DIGEST_LINE.fullmatch(line)]
            if keep != [line for line in b if not DIGEST_LINE.fullmatch(line)]:
                refusals.append(f"{path}: [package_digest] changed a line that is not a digest")
            continue
        if header not in (None, "[[patch]]"):
            if a != b:
                refusals.append(f"{path}: {header} changed")
            continue
        if len(a) != len(b) or any(
            x != y and not (KEY_LINE.fullmatch(x) and KEY_LINE.fullmatch(y) and x[:7] == y[:7])
            for x, y in zip(a, b)
        ):
            refusals.append(
                f"{path}: {header or 'the top'} changed a line other than `version` or `sha256`"
            )
    try:
        old_data, new_data = tomllib.loads(old_text), tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as error:
        return refusals + [f"{path}: not valid TOML ({error})"]
    for data in (old_data, new_data):
        data.pop("package_digest", None)
        data.pop("version", None)
        data.pop("sha256", None)
        for patch in data.get("patch", []):
            patch.pop("version", None)
            patch.pop("sha256", None)
    if old_data != new_data:
        refusals.append(f"{path}: its parsed tables differ outside the version and digest keys")
    return refusals


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True, cwd=BASALT).stdout


def confined(name, base, head, results_file):
    changed = [
        tuple(row.split("\t", 1))
        for row in git("diff", "--name-status", "--no-renames", base, head).splitlines()
    ]
    path = f"recipes/{name}.toml"
    refusals = confined_refusals(
        name, changed, git("show", f"{base}:{path}"), git("show", f"{head}:{path}")
    )
    results = load_results(results_file)
    results["confined"] = (
        {"ok": False, "why": "; ".join(refusals)}
        if refusals
        else {"ok": True, "why": f"only the version and digest lines of {path} changed"}
    )
    save(results_file, results)
    print(f"recipe_bump: confined: {results['confined']['why']}")


# --- the decision --------------------------------------------------------------------------------

# The checks whose failure asks a person (§328, clause 3), and how each is named in a reason.
HOLDING = {
    "note": "endowment",
    "license": "license",
    "patch": "patch",
    "confined": "recipe-only diff",
}


def decide(results, eligible_at, now):
    """("arm" | "hold" | "wait", [reasons]). A check that did not run holds, as a failed one does."""
    held = []
    for key, label in HOLDING.items():
        result = results.get(key)
        if result is None:
            held.append(f"{label}: the check did not run")
        elif not result["ok"]:
            held.append(f"{label}: {result['why']}")
    if held:
        return "hold", held
    if now < eligible_at:
        return "wait", [
            f"hold: the release is younger than the hold, and may merge from {eligible_at.isoformat()}"
        ]
    return "arm", [f"{label}: {results[key]['why']}" for key, label in HOLDING.items()]


def decide_command(results_file, eligible_at, now=None):
    now = now or datetime.datetime.now(datetime.UTC)
    outcome, reasons = decide(load_results(results_file), parse_time(eligible_at), now)
    print(f"outcome={outcome}")
    # One reason per line, in GitHub's multi-line output form.
    print("reasons<<REASONS")
    for reason in reasons:
        print(f"- {reason}")
    print("REASONS")


def load_results(path):
    path = pathlib.Path(path)
    return json.loads(path.read_text()) if path.exists() else {}


def save(path, results):
    pathlib.Path(path).write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")


def watched():
    names = []
    for path in sorted((BASALT / "recipes").glob("*.toml")):
        recipe = tomllib.loads(path.read_text())
        if source_of(recipe):
            names.append(recipe["name"])
        else:
            reason = NOT_WATCHED.get(recipe["name"], "it names no crate")
            print(f"recipe_bump: {recipe['name']}: not yet watched: {reason}", file=sys.stderr)
    print(json.dumps(names))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("watched")
    p = sub.add_parser("plan")
    p.add_argument("recipe")
    p.add_argument("--version", default=None)
    r = sub.add_parser("rewrite")
    for arg in ("recipe", "version", "sha256"):
        r.add_argument(arg)
    r.add_argument("work", type=pathlib.Path)
    c = sub.add_parser("check")
    c.add_argument("recipe")
    c.add_argument("work", type=pathlib.Path)
    c.add_argument("results")
    s = sub.add_parser("served")
    for arg in ("recipe", "version", "arch", "dest"):
        s.add_argument(arg)
    t = sub.add_parser("note-tool")
    t.add_argument("nife")
    n = sub.add_parser("note")
    for arg in ("results", "exit", "output"):
        n.add_argument(arg)
    g = sub.add_parser("confined")
    for arg in ("recipe", "base", "head", "results"):
        g.add_argument(arg)
    d = sub.add_parser("decide")
    d.add_argument("results")
    d.add_argument("eligible_at")
    a = parser.parse_args()
    if a.verb == "watched":
        watched()
    elif a.verb == "plan":
        plan(a.recipe, a.version or None)
    elif a.verb == "rewrite":
        rewrite(a.recipe, a.version, a.sha256, a.work)
    elif a.verb == "check":
        check(a.recipe, a.work, a.results)
    elif a.verb == "served":
        served(a.recipe, a.version, a.arch, a.dest)
    elif a.verb == "note-tool":
        note_tool(a.nife)
    elif a.verb == "note":
        note(a.results, a.exit, a.output)
    elif a.verb == "confined":
        confined(a.recipe, a.base, a.head, a.results)
    elif a.verb == "decide":
        decide_command(a.results, a.eligible_at)


if __name__ == "__main__":
    os.umask(0o022)
    main()
