"""Open the NVDA Add-on Store submission form for the current release, filled in.

Prevents: submitting the wrong download link or version to the store, for
example a link to a release that doesn't exist yet or to an older package,
which makes NV Access's checks fail or publishes the wrong Typix.

Run after the GitHub release exists:  python tools/submit_store.py
It reads the version, author and repository link from manifest.ini, downloads
the released package and checks that the version inside it matches, checks
that this version hasn't been submitted already, and opens the form in the
default browser. You then set Channel to stable, which GitHub can't fill
in from a link, and press Create. It can't press Create for you: the
store's workflow only runs for issues made through the web form, which carry
the autoSubmissionFromIssue label.

It can't see whether the package works; build and test it first.
"""

import io
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import webbrowser
import zipfile

import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASTORE = "nvaccess/addon-datastore"
LICENSE_NAME = "GPL v2"
LICENSE_URL = "https://www.gnu.org/licenses/gpl-2.0.html"


def manifest_field(text, name):
    m = re.search(r'^%s\s*=\s*"?([^"\r\n]+?)"?\s*$' % name, text, re.MULTILINE)
    if not m:
        sys.exit("manifest.ini has no %s line." % name)
    return m.group(1).strip()


def main():
    with open(os.path.join(REPO, "manifest.ini"), encoding="utf-8") as f:
        manifest = f.read()
    name = manifest_field(manifest, "name")
    version = manifest_field(manifest, "version")
    author = manifest_field(manifest, "author")
    source = manifest_field(manifest, "url")
    download = "%s/releases/download/v%s/%s-%s.nvda-addon" % (source, version, name, version)

    print("Checking %s" % download)
    try:
        with urllib.request.urlopen(download, timeout=60) as response:
            data = response.read()
    except Exception as e:
        sys.exit("The release package can't be downloaded (%s). Create the GitHub release first." % e)
    try:
        packed = zipfile.ZipFile(io.BytesIO(data)).read("manifest.ini").decode("utf-8")
    except Exception as e:
        sys.exit("The download isn't a valid add-on package (%s)." % e)
    if manifest_field(packed, "version") != version or manifest_field(packed, "name") != name:
        sys.exit("The released package is %s %s, not %s %s." % (
            manifest_field(packed, "name"), manifest_field(packed, "version"), name, version))

    try:
        found = subprocess.run(
            ["gh", "issue", "list", "-R", DATASTORE, "--state", "all", "--search",
             '"%s-%s.nvda-addon" in:body' % (name, version), "--json", "number,url"],
            capture_output=True, text=True, timeout=60,
        )
        existing = json.loads(found.stdout or "[]") if found.returncode == 0 else []
    except (OSError, ValueError, subprocess.TimeoutExpired):
        existing = []
        print("Could not check for an earlier submission (is gh installed?); going on.")
    if existing:
        sys.exit("Version %s was already submitted: %s" % (version, existing[0]["url"]))

    query = urllib.parse.urlencode({
        "template": "registerAddon.yml",
        "title": "[Submit add-on]: %s" % name,
        "download-url": download,
        "source-url": source,
        "publisher": author,
        "channel": "stable",
        "license-name": LICENSE_NAME,
        "license-url": LICENSE_URL,
    })
    webbrowser.open("https://github.com/%s/issues/new?%s" % (DATASTORE, query))
    # GitHub fills in text fields from the link but not drop-down lists.
    print("Opened the form for %s %s. Set Channel to stable, then press Create." % (name, version))


if __name__ == "__main__":
    main()
