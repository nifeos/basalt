**Lane:** <branch>, written by an agent; delete this line if a person wrote this pull request.

## What changed, and why

<!-- The diff shows what. Say why, and say what you decided against. -->

## Recipes

<!--
For a new or changed recipe: the upstream version and where its SHA-256 came from, the `license`
and `license_files`, what documentation upstream ships, and any patch with the reason it exists.
Say "none" if no recipe changed.
-->

## Pins

<!--
A pin bump names the landed commit it takes (`nifeos/<repo>@<sha>`). A pin never points at an
unmerged branch. Say "none" if no pin changed.
-->

## Gates

- [ ] `helpers/packages.py check`
- [ ] The seven required checks are green on this pull request
