# basalt

The first-party distribution of [nife](https://github.com/nifeos/nife).

The naming convention, so nobody has to ask: the kernel is named for the core (`nife`, Suess's
nickel-iron), distributions are named for rocks, and programs for minerals. A rock is an
aggregate of minerals the way a distribution is an aggregate of programs. There are hundreds of
named rocks, so naming your own distribution requires no permission and no coordination with
this project; only `basalt` is reserved.

What a distribution actually packages is an open question, recorded in the nife tree
([`design/what-a-distribution-packages.md`](https://github.com/nifeos/nife/blob/main/design/what-a-distribution-packages.md)).

## What is here today

The smallest thing that is still a distribution: one component, pinned, and a gate that proves the
pin builds and passes its own system test. Nothing has moved out of the nife repository, and nife
does not know basalt exists.

- [`pins.toml`](pins.toml) names each component by repository and full commit id. Today that is one
  entry, `nife`.
- [`gate`](.github/workflows/gate.yml) checks out the pinned commit and runs nife's own
  `script/ci-build test` there: the toolchain and QEMU that commit names, then each architecture's
  kernel and programs built and the system test suite booted under QEMU on aarch64, riscv64 and
  x86_64. The test kernels and program archives are kept as the run's artifact for 14 days.
- [`pin bump`](.github/workflows/pin-bump.yml) runs daily at 06:17 UTC. If nife's `main` has moved,
  it rewrites the pin on the branch `pin/nife` and opens (or updates) one pull request, and the gate
  runs on it.
- [`helpers/pins.py`](helpers/pins.py) is the one reader and writer of `pins.toml`.
- [`recipes/`](recipes/) holds one recipe per package basalt builds. Today that is
  [`ripgrep`](recipes/ripgrep.toml): the crate and its one patched dependency, each pinned by
  SHA-256, and the program it installs as (`rg`).
- [`helpers/packages.py`](helpers/packages.py) builds every recipe against a nife checkout, with
  that checkout's toolchain, standard library, manifest note and `cargo xtask package`, and writes
  a repository in the layout nife's `jig` reads: `rolling/metadata/stand-in-index` and
  `rolling/targets/<stem>.nifepkg`.
- [`packages`](.github/workflows/packages.yml) runs that build at the pin, keeps the repository as
  the run's artifact, and boots the pinned nife's interactive check with the repository named in
  `NIFE_BASALT_REPOSITORY`, so `jig update`, `jig install ripgrep` and `rg --version` are typed at
  the prompt on aarch64, riscv64 and x86_64 (nife milestone 810).
- Its third job, `publish`, runs on `main` after both: the packages go on a GitHub Release and the
  index on GitHub Pages at `basalt.nifeos.org`, as the next section says.

## Where basalt is served

calef ruled on 2026-10-12 (UTC). Asked whether `basalt.nifeos.org` should be a VPS running Caddy,
GitHub Pages, Cloudflare, or S3 and CloudFront, he answered *"b"*, GitHub Pages. Asked then how
adoption would be measured, he answered *"Packages on Releases, index on Pages"*. Later the same
day: basalt must not become the organization's Pages site, because `www.nifeos.org` will be its
own site from `nifeos/nifeos.github.io`.

| What | Where | Why |
|---|---|---|
| The index, `rolling/metadata/stand-in-index` | GitHub Pages, the project site of this repository, at its own custom domain `https://basalt.nifeos.org/` | Every nife image names this host and pins its TLS to ISRG Root X1 (nife §250); Pages certificates are Let's Encrypt and chain to X1 |
| The packages, `<stem>.nifepkg` | One GitHub Release per publish, tagged `rolling-<UTC date of the commit>-<short commit>` (provisional) | Each asset's `download_count` is the first adoption signal, per package, version and architecture (nife milestone 576). Nothing identifies a system |
| basalt's own copy of the packages, `rolling/targets/` | The same Pages site | nife §250's amendment of 2026-10-10, clause 9: basalt always keeps a copy under its own `targets/`, tried last. It is also the only copy `jig` can fetch today (BUGS) |

Each index line names its package's Release asset as a listed location,
`<stem> sha256:<hex> https://github.com/nifeos/basalt/releases/download/<tag>/<stem>.nifepkg`.
The digest decides whether the bytes are believed, wherever they came from. A system-level count
like Debian's popcon or Fedora's countme needs an endpoint whose logs someone reads, and is later
work.

## What calef must do before the first publish

The `publish` job refuses to run until step 3 is done, and says what is missing.

1. In the DNS zone for `nifeos.org` (Netlify DNS, measured 2026-10-13 UTC), add
   `basalt CNAME nifeos.github.io.` and nothing else for that name.
2. Recommended first, so nobody else can claim the name: verify `nifeos.org` for the organization
   (organization Settings, Pages, "Add a domain"; it asks for one TXT record,
   `_github-pages-challenge-nifeos.nifeos.org`).
3. In this repository's Settings, Pages: Source "GitHub Actions"; Custom domain
   `basalt.nifeos.org`; once the certificate is issued, tick "Enforce HTTPS". Do not create
   `nifeos/nifeos.github.io` for this. basalt is a project site, and its custom domain must be set
   here, or once the organization's site takes `www.nifeos.org` this index would be served at
   `www.nifeos.org/basalt`, a name no image carries.
4. Land #12, then #13, then run `packages` on `main` (`gh workflow run packages.yml`) or let the
   next pin bump do it.
5. Check the chain an image will see:
   `openssl s_client -connect basalt.nifeos.org:443 -servername basalt.nifeos.org </dev/null | grep 'i:'`
   ends at `ISRG Root X1`.

basalt has no toolchain of its own. It builds each component at the toolchain pin that component's
commit carries.

Every name here (the manifest's file name, its keys, the workflows, the branch `pin/nife`, the
Release tags) is provisional.

## EXAMPLES

Pin nife at a specific commit by hand and see whether it passes:

```sh
helpers/pins.py set nife 0123456789abcdef0123456789abcdef01234567
git switch -c pin/try && git commit -am "pin nife at 012345678" && git push -u origin pin/try
gh pr create --fill --draft      # the gate runs on the pull request
```

Ask the pin bump to propose something other than main's head (after it is on `main`):

```sh
gh workflow run pin-bump.yml -f commit=0123456789abcdef0123456789abcdef01234567
```

Read the current pin:

```sh
$ helpers/pins.py get nife
repository=https://github.com/nifeos/nife
slug=nifeos/nife
commit=e2ee497174609a23288898a355c933d639dda53c
```

## BUGS

- **Nothing is published until calef's steps above are done.** Until then the repository lives
  only as a workflow artifact, and the check serves it from nife's test index host.
- **`jig` cannot fetch a Release asset yet, so it installs from the Pages copy and nothing is
  counted.** A Release download starts at `github.com`, whose certificate chains to USERTrust ECC
  (Sectigo), and redirects to `release-assets.githubusercontent.com` with a signed URL that lasts
  about an hour. `jig` pins ISRG Root X1 for every host and follows no redirect, so it passes over
  the listed location and falls back to `rolling/targets/` on Pages. Which way to fix that is asked
  on nifeos/nife#2048. If the answer is to keep only the Release, `publish` stops copying
  `targets/` (one line in `helpers/packages.py`).
- **The index copy `jig` keeps is one page per architecture**, 4,095 bytes. With a listed location
  a line is about 200 bytes, so about twenty packages per architecture fit.
- **A Release is never pruned.** Pages carries only the newest build; old versions stay reachable
  on their Releases. GitHub's documented limits (Pages: a 1 GB site and a soft 100 GB a month;
  Releases: 2 GiB an asset) are recalled, not measured here.
- **A recipe records the source's digest, not the package's.** Recording the package's needs the
  build to be reproducible across machines, which nobody has measured; the index carries the
  digest of what this run built.
- **`helpers/packages.py` knows one recipe shape**, a crate with one patched dependency and one
  program, because that is all there is.

- **The gate is one job, so a failing architecture can hide the ones after it.** `script/ci-build
  test` runs the three legs in order in one process. A per-architecture matrix would show each leg's
  status separately at roughly three times the provisioning cost per run.
- **The system test runs a second time for every pin.** nife's merge queue already ran it on the
  same commit. That is the price of the gate existing here before a split needs it here.
- **The bump depends on the automation App.** It mints an installation token from the secrets
  `AUTOMATION_APP_ID` and `AUTOMATION_APP_KEY`. Without them it falls back to `github.token`, which
  starts no workflow (the bump then starts the gate itself) and cannot open a pull request unless
  "Allow GitHub Actions to create and approve pull requests" is on.
- **Nothing protects `main` yet.** There is no ruleset, no required check and no merge queue, so the
  gate reports but does not block, and the bump's pull request is merged by a person.
- **GitHub suspends scheduled workflows after 60 days with no activity in the repository.** A bump
  pull request nobody merges is not activity.
- **Until the gate has run on `main`, every pull request builds QEMU from source** (about 4.3
  minutes of a 15-minute run). A cache saved by a pull request is visible only to that pull request,
  and a red run saves none.
- **Only github.com repositories can be pinned**, because the gate checks out with
  `actions/checkout`.
- **The kept "images" are the test images**, the kernels the system test boots and their program
  archives, not an installable image. A red that fails before the images are built (a host test, a
  compile error) keeps nothing.
