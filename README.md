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

basalt has no toolchain of its own. It builds each component at the toolchain pin that component's
commit carries.

Every name here (the manifest's file name, its keys, the workflows, the branch `pin/nife`) is
provisional.

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
