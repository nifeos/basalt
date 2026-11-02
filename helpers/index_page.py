#!/usr/bin/env python3
"""Write the site's front page, listing what `publish` put under rolling/ (name provisional).

Usage:
    helpers/index_page.py <template> <site-dir>

The template is web/index.html. Its three markers, @@METADATA@@, @@SOURCES@@ and @@TARGETS@@, are
replaced by lists of links to the files actually under <site-dir>/rolling/metadata, sources and
targets, so a link never names a file the site lacks. The result is <site-dir>/index.html. Links
are relative, so one page serves every address the site has. A missing directory or an empty one
is an error: the page would be wrong, and publishing it would hide that.
"""

import html
import pathlib
import sys
import urllib.parse

SECTIONS = (("@@METADATA@@", "metadata"), ("@@SOURCES@@", "sources"), ("@@TARGETS@@", "targets"))


def size(n):
    for unit in ("bytes", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n} bytes" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


def listing(rolling, name):
    directory = rolling / name
    files = sorted(p for p in directory.iterdir() if p.is_file()) if directory.is_dir() else []
    if not files:
        sys.exit(f"index_page: {directory} holds no files; refusing to write a page that lies")
    items = []
    for p in files:
        href = f"rolling/{name}/{urllib.parse.quote(p.name)}"
        items.append(
            f'  <li><a href="{href}">{html.escape(p.name)}</a>'
            f' <span class="size">({size(p.stat().st_size)})</span></li>'
        )
    return f'<ul aria-label="{name}">\n' + "\n".join(items) + "\n</ul>"


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    template, site = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
    page = template.read_text()
    for marker, name in SECTIONS:
        if page.count(marker) != 1:
            sys.exit(f"index_page: {template} must hold {marker} exactly once")
        page = page.replace(marker, listing(site / "rolling", name))
    (site / "index.html").write_text(page)
    print(f"index_page: wrote {site / 'index.html'}")


if __name__ == "__main__":
    main()
