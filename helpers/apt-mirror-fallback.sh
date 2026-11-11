#!/bin/sh
# Keep apt usable on a GitHub-hosted runner while Ubuntu's own archive hosts do not resolve.
#
#     helpers/apt-mirror-fallback.sh
#
# Why it exists: a hosted runner's apt sources name azure.archive.ubuntu.com and security.ubuntu.com
# (amd64) or ports.ubuntu.com (arm64). When Canonical's DNS is down (2026-11-11), every
# `apt-get update` in a job fails or leaves the package lists empty, including the ones inside the
# pinned nife commit's script/bootstrap and script/ci-qemu, which basalt does not own and cannot
# change. Run this before the first of them.
#
# What it does: always sets Acquire::Retries for later apt runs. Then, only if none of the Ubuntu
# archive hosts the sources name resolves, it rewrites those hosts to a mirror that serves the same
# archive: mirrors.edge.kernel.org/ubuntu for amd64 and mirrors.mit.edu/ubuntu-ports for arm64
# (kernel.org does not carry ubuntu-ports; checked 2026-11-11 with curl on dists/noble/InRelease).
# It reads deb822 sources, one-line sources and the mirror list a `mirror+file:` source names
# (/etc/apt/apt-mirrors.txt). When the names resolve it changes nothing else.
#
# Signature verification stays on. The mirror serves Ubuntu's own signed Release files, which apt
# checks against the archive keys the image already trusts, so a mirror can withhold a package but
# cannot alter one. This script never passes `trusted=yes` or `--allow-unauthenticated`.
#
# Name: provisional, minted by lane/apt-mirror-fallback.

set -eu

as_root=sudo
[ "$(id -u)" -eq 0 ] && as_root=

printf 'Acquire::Retries "3";\n' | $as_root tee /etc/apt/apt.conf.d/80-retries >/dev/null

main_mirror=http://mirrors.edge.kernel.org/ubuntu/
ports_mirror=http://mirrors.mit.edu/ubuntu-ports/

sources="/etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources"
main_uri='https?://([a-z0-9-]+\.)*(archive|security)\.ubuntu\.com/ubuntu'
ports_uri='https?://ports\.ubuntu\.com/ubuntu-ports'

# The files that hold an Ubuntu URI: the source files, and each mirror list a `mirror+file:` source
# names.
# shellcheck disable=SC2086 # $sources holds globs on purpose.
ubuntu_files() {
    for file in $sources $(cat $sources 2>/dev/null | grep -Eo 'mirror\+file:[^ ]+' | sed 's/^mirror+file://' | sort -u); do
        [ -f "$file" ] && grep -Eq "$main_uri|$ports_uri" "$file" && echo "$file"
    done
}

hosts=$(ubuntu_files | xargs -r cat | grep -Eo "$main_uri|$ports_uri" | sed -E 's#^https?://##; s#/.*##' | sort -u)
for host in $hosts; do
    if getent hosts "$host" >/dev/null; then
        echo "apt-mirror-fallback: $host resolves; sources left as they are"
        exit 0
    fi
done

echo "apt-mirror-fallback: no Ubuntu archive host resolves; switching sources to $main_mirror and $ports_mirror" >&2
for file in $(ubuntu_files); do
    $as_root sed -Ei "s#$ports_uri/?#$ports_mirror#g; s#$main_uri/?#$main_mirror#g" "$file"
    echo "apt-mirror-fallback: rewrote $file" >&2
done
