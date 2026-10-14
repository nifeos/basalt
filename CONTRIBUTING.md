# Contributing to basalt

basalt is nife's first-party distribution. It builds other projects' programs, and nife's own,
against a pinned nife commit and publishes them as packages `jig` installs. Most contributions are
a new recipe or a version bump. The rules for agents are in [`AGENTS.md`](AGENTS.md); this file is
what a person needs.

## Adding a recipe

A recipe is one file, `recipes/<package>.toml`, read by `helpers/packages.py`. Copy the closer of
the two that exist: [`ripgrep`](recipes/ripgrep.toml) for a Rust crate from crates.io,
[`ioping`](recipes/ioping.toml) for C compiled against nife's C library. Every recipe needs:

- `name`, `version` and `program`: the package, its upstream version, and the program it installs.
- **The source, pinned by SHA-256.** A crate's `sha256` is the `cksum` in crates.io's index; a C
  recipe's `[[source]]` entries each name a URL and the SHA-256 of its bytes. The build refuses
  anything else.
- **`license`**, the upstream license as an SPDX expression (`"GPL-3.0-or-later"`,
  `"Unlicense OR MIT"`), and **`license_files`**, the license texts inside the upstream source. The
  package carries them as `doc/<file name>`. A C recipe fetches its license file as a `[[source]]`
  like any other.
- **Documentation.** nife milestone 918 (a package carries its program's documentation) makes a
  recipe name the upstream documents its package ships; until it lands, say in the recipe's header
  what upstream ships so the recipe is ready for it.
- A patch, if the program needs one on nife, under `patches/` with a header that says why it exists
  and how to show it is still needed. The recipe names it with its SHA-256.

The header comment says what is pinned and why. Every key is provisional.

Then run `helpers/packages.py check` and open a pull request. The `packages` workflow builds every
recipe against nife's pin and boots nife to install them, so you do not need a nife toolchain
locally, though `helpers/packages.py build <nife checkout> repository` works with one.

## What the gates check

Four checks are required before anything merges into `main`:

| Check | What it proves |
|---|---|
| build the packages at nife's pin | every recipe names a license and license files, every source matches its SHA-256, every patch matches its recipe, and each package builds for aarch64, riscv64 and x86_64 |
| jig installs them (swish-check-aarch64-riscv64) | a booted nife on aarch64 and riscv64 installs the packages with `jig` and runs them |
| jig installs them (swish-check-x86_64) | the same on x86_64 |
| nife at its pin (image build and system test, three architectures) | the pinned nife commit builds and passes its own system test |

`lint` (ruff on `helpers/*.py`, typos on the tree) and CodeQL run too, and are not required.

## Licenses

basalt's own files are MIT OR Apache-2.0. A contribution to them is offered under the same terms. A
patch under `patches/` is offered under the license of the upstream project it patches.

## Reporting a problem

A package that should not be installed goes to [SECURITY.md](SECURITY.md), privately. Everything
else, including a package request, is an issue.
