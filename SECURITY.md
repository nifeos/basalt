# Security policy

basalt is nife's first-party distribution: recipes that pin other projects' source by SHA-256, a
build that compiles them against a pinned nife commit, and a workflow that publishes the packages
on GitHub Releases and their index on GitHub Pages at `basalt.nifeos.org`. What basalt claims is
that a package is built from exactly the source its recipe names and that the index vouches only
for what that build produced. A report that breaks either claim is the most useful one this
repository can receive.

A flaw in nife itself (a confined program escaping, a kernel panic from user space, `jig` accepting
bytes the index does not vouch for) belongs to nife: see
[nife's SECURITY.md](https://github.com/nifeos/nife/blob/main/SECURITY.md).

## Reporting

Email **chris@crickertech.com** with `basalt security` in the subject. There is no PGP key; if you
need one before you will send details, say so in a first message and we will sort out a channel.

**Please do not open a public issue for something you believe is exploitable**, such as a package
that should not be installed. Everything else, including a license problem, belongs in a public
issue.

### What makes a report actionable

- The **package stem** (`<name>-<version>-<architecture>`) and the **Release tag** or index you
  fetched it from, or the basalt commit you built at.
- What is wrong with it, in the terms below.
- How you checked: the digest you computed, the recipe line it contradicts, the run log, or the
  commands that reproduce it.

## In scope

- **A bad package.** Bytes on a Release or on Pages that the index does not vouch for; a package
  that was not built from the source its recipe pins; a pinned upstream version that is itself
  compromised or malicious; a patch under `patches/` that does something its header does not say.
- **The build.** A way for a pull request, a recipe or a fetched source to run code with the
  publish job's permissions, to change what `publish` uploads, or to make the build accept a source
  whose SHA-256 is not the recipe's.
- **The index.** A line `jig` would accept that names a location or digest the build did not
  produce.
- **Secrets.** A token or key in the tree, in a workflow log, or in a published artifact.
- **Licensing that leaves a binary without its source.** A published package with no source
  archive beside it, or whose archive is not what it was built from.

## Not in scope

- Bugs in an upstream program that basalt packages unchanged. Report those upstream; tell us too if
  basalt should stop shipping the version.
- That nife is a demonstrator and not ready for production. That is the premise.

## What to expect

- **Acknowledgement within about a week.** This is a side project; there is no on-call.
- **No fixed remediation timeline, and no bounty.** A bad package is pulled from the index first
  and explained after; a Release asset is never replaced in place, so a fix is a new Release.
- **Credit however you want it.** Say if you would rather not be named.
- **Coordinated disclosure, preferred and not demanded.** Publishing after 90 days is fine whether
  or not there is a fix. If the finding is in upstream code, upstream's timeline wins over ours.

## Supported versions

`main` and the newest `rolling-*` Release. Older Releases stay downloadable and are not patched.
