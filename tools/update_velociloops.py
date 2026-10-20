"""Update the vendored VelociLoops (third_party/velociloops) from GitHub.

    uv run python tools/update_velociloops.py              # newest commit on upstream's default branch
    uv run python tools/update_velociloops.py --ref v1.2   # a tag, branch or commit
    uv run python tools/update_velociloops.py --dry-run    # show what would change, write nothing

Run by hand, never from a build: the library parses untrusted files in C++, so a new version should be a diff you review
and commit. Everything is downloaded before anything is written (an upstream layout change aborts with the tree untouched).
The commit is recorded in third_party/velociloops/VERSION and in the two docs that quote it. Then it compiles the library
and runs the .rx2 tests. Review `git diff third_party` before committing.
"""

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VENDOR_DIR = os.path.join(ROOT, "third_party", "velociloops")
VERSION_FILE = os.path.join(VENDOR_DIR, "VERSION")

REPO = "kunitoki/VelociLoops"
# upstream path -> file in third_party/velociloops (flattened)
FILES = {
    "include/velociloops.h": "velociloops.h",
    "src/velociloops.cpp": "velociloops.cpp",
    "src/fft8g.h": "fft8g.h",
    "LICENSE": "LICENSE",
}
# docs that quote the commit as "commit `abc1234`"
DOCS_QUOTING_THE_COMMIT = ("AGENTS.md", os.path.join("src", "ui", "help", "THIRD_PARTY_NOTICES.md"))
_COMMIT_QUOTE = re.compile(r"commit `[0-9a-f]{7}`")


def read_version(path=VERSION_FILE):
    """The pinned commit sha, or None if there is no valid VERSION file."""
    try:
        with open(path, encoding="utf-8") as f:
            sha = f.read().strip()
    except OSError:
        return None
    return sha if re.fullmatch(r"[0-9a-f]{40}", sha) else None


def replace_commit_quote(text, sha):
    return _COMMIT_QUOTE.sub(f"commit `{sha[:7]}`", text)


def summarize_change(old, new):
    """'unchanged', 'new file' or '+A -B lines' for one file's old and new text (None = absent)."""
    if old is None:
        return "new file"
    if old == new:
        return "unchanged"
    added = removed = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return f"+{added} -{removed} lines"


def _get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AKAISDS-update-velociloops"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def resolve_ref(ref):
    """Full commit sha for a ref ('HEAD' = upstream's default branch)."""
    data = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits/{ref}"))
    sha = data["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit(f"Unexpected commit id from GitHub: {sha!r}")
    return sha


def fetch(sha):
    """{local file name: text} for every vendored file at *sha*; raises SystemExit if any is missing."""
    fetched = {}
    for upstream_path, local_name in FILES.items():
        try:
            fetched[local_name] = _get(
                f"https://raw.githubusercontent.com/{REPO}/{sha}/{upstream_path}"
            ).decode("utf-8")
        except urllib.error.HTTPError as e:
            raise SystemExit(
                f"{upstream_path} is not at {sha[:7]} (HTTP {e.code}) - upstream's layout changed; nothing written."
            )
    return fetched


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ref", default="HEAD", help="tag, branch or commit (default: upstream's latest)")
    parser.add_argument("--dry-run", action="store_true", help="show the changes, write nothing")
    parser.add_argument("--no-test", action="store_true", help="skip the compile and test run afterwards")
    args = parser.parse_args(argv)

    current = read_version()
    sha = resolve_ref(args.ref)
    print(f"VelociLoops: pinned {current[:7] if current else 'none'} -> {sha[:7]} ({args.ref})")
    if sha == current:
        print("Already at that commit.")
        return 0

    fetched = fetch(sha)
    for name, text in fetched.items():
        print(f"  {name:18s} {summarize_change(_read(os.path.join(VENDOR_DIR, name)), text)}")
    if args.dry_run:
        print("Dry run - nothing written.")
        return 0

    for name, text in fetched.items():
        with open(os.path.join(VENDOR_DIR, name), "w", encoding="utf-8", newline="") as f:
            f.write(text)
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        f.write(sha + "\n")
    for doc in DOCS_QUOTING_THE_COMMIT:
        path = os.path.join(ROOT, doc)
        text = _read(path)
        if text is not None:
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(replace_commit_quote(text, sha))
    print(f"Wrote third_party/velociloops and VERSION; updated the commit quoted in {', '.join(DOCS_QUOTING_THE_COMMIT)}.")

    if args.no_test:
        return 0
    sys.path.insert(0, os.path.dirname(__file__))
    import build_velociloops

    build_velociloops.build()
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/test_rx2.py", "-q"], cwd=ROOT)
    if result.returncode:
        print("The .rx2 tests FAILED with the new version - `git checkout third_party` to go back.")
        return result.returncode
    print("Built and tested. Review `git diff third_party`, then commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
