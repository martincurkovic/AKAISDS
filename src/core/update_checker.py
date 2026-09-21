"""Checks GitHub Releases for a newer AKAISDS version.

Uses the unauthenticated GitHub REST API (`/releases/latest`), which only
ever returns the latest *published* (non-draft, non-prerelease) release -
exactly matching how `.github/workflows/build.yml` publishes builds.
"""

from dataclasses import dataclass
import json
import re
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

try:
    from ui._version import APP_VERSION
except ImportError:
    APP_VERSION = "0.0.0-dev"  # matches the build scripts' own dev fallback

REPO = "martincurkovic/AKAISDS"
RELEASES_API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
REQUEST_TIMEOUT = 5  # seconds - this must never hang app startup

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass
class UpdateInfo:
    version: str  # e.g. "1.1.0", no leading "v"
    tag: str  # e.g. "v1.1.0"
    name: str  # release title, may be blank
    html_url: str  # GitHub release page to send the user to


def _parse_version(version_string):
    # returns an (major, minor, patch) tuple, or None if this isn't a clean
    # released version number (dev builds, anything with a -suffix, etc)
    match = _VERSION_RE.match(version_string.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def check_for_update(current_version=APP_VERSION):
    """Returns an UpdateInfo if a newer release exists, else None.

    Raises urllib.error.URLError/HTTPError or ValueError on network/parse
    failure - callers decide whether that's worth surfacing to the user.
    """
    current = _parse_version(current_version)
    if current is None:
        # dev/unreleased build - nothing meaningful to compare against
        return None

    request = urllib.request.Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub's API rejects requests with no User-Agent
            "User-Agent": f"AKAISDS/{current_version}",
        },
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        payload = json.load(response)

    tag = payload.get("tag_name", "")
    latest = _parse_version(tag)
    if latest is None or latest <= current:
        return None

    return UpdateInfo(
        version=".".join(str(part) for part in latest),
        tag=tag,
        name=payload.get("name") or tag,
        html_url=payload.get("html_url", f"https://github.com/{REPO}/releases"),
    )


class UpdateCheckWorker(QThread):
    """Runs check_for_update() off the UI thread.

    One-shot, throwaway worker - unlike BridgeWorker this touches no shared
    hardware connection, so there's no need for a single long-lived thread
    serializing requests (see AGENTS.md's BridgeWorker section for why that
    rule exists elsewhere in this codebase and doesn't apply here).
    """

    succeeded = Signal(object)  # UpdateInfo | None
    failed = Signal(str)

    def run(self):
        try:
            info = check_for_update()
        except Exception as exc:  # network errors, bad JSON, etc.
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(info)
