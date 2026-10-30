# Working on basalt

basalt is nife's first-party distribution: [`pins.toml`](pins.toml) pins each component at a
commit, [`recipes/`](recipes/) says how each package is built, and the workflows build, test and
publish them. Read the workspace's front page first (`CLAUDE.md` in the directory that holds every
nifeos checkout); it says which repository owns what and how a change crosses repositories. For
commits, naming and decisions, nife's skills under
[`notes/skills/`](https://github.com/nifeos/nife/tree/main/notes/skills) apply here too.

## basalt's own rules

- **A pin never points at an unmerged branch.** A change lands in the repository that owns it
  first; the pin bump that takes it is a separate pull request citing the landed commit
  (`nifeos/<repo>@<sha>`), and records the dependency on its side only.
- **Every recipe names its license and its license files.** `license` is an SPDX expression and
  `license_files` are paths inside the upstream source. `helpers/packages.py check` refuses a recipe
  without them, and the build runs it first.
- **Every recipe ships its documentation** once nife milestone 918 gives packages a `doc/` bundle.
  Until then a recipe's header says what upstream ships.
- **Every published package has its source beside it.** The build writes a source archive per
  package and `publish` puts it on the Release and on Pages. Never publish a package by hand
  without one.
- **A Release asset is never replaced.** A fix is a new Release.
- **New names are provisional.** File paths, recipe keys, workflow and job names, and Release tags
  ship marked provisional; the required check names are fixed by the repository's ruleset and not
  renamed, except the two composed ones, which stay provisional.
- **Machine-local facts stay out.** Nothing true only of one person's machines goes in this
  public repository.

Commits end with a `Co-Authored-By:` trailer naming the model. Never use `git stash` or
`--no-verify`.

Name of this file: provisional, after nife's `AGENTS.md`.
