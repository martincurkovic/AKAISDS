"""Checks GitHub Releases for a newer AKAISDS version.

Uses the unauthenticated GitHub REST API (`/releases`, the most recent
ones). Only published (non-draft, non-prerelease) releases count, exactly
matching how `.github/workflows/build.yml` publishes builds. The newest of
them is the update; the notes shown are those of every release newer than the
installed version, newest first, so someone several versions behind reads
everything they missed.
"""

from dataclasses import dataclass
import json
import re
import ssl
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

from core import debug_log

try:
    from ui._version import APP_VERSION
except ImportError:
    APP_VERSION = "0.0.0-dev"  # matches the build scripts' own dev fallback

REPO = "martincurkovic/AKAISDS"
RELEASES_PER_PAGE = 30  # how many recent releases are asked for: more than anyone is behind by
RELEASES_API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page={RELEASES_PER_PAGE}"
REQUEST_TIMEOUT = 5  # seconds - this must never hang app startup

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


@dataclass
class UpdateInfo:
    version: str  # e.g. "1.1.0", no leading "v"
    tag: str  # e.g. "v1.1.0"
    name: str  # release title, may be blank
    html_url: str  # GitHub release page to send the user to
    notes: str = ""  # release notes as Markdown: those of every release newer than the installed one, newest first


def _ssl_context():
    # A bundled Python (the Nuitka macOS build) has no system CA store, so the
    # default context fails with CERTIFICATE_VERIFY_FAILED. certifi ships its
    # own bundle; if it is missing, fall back to the platform default.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        debug_log.get_logger().warning(
            "update_checker: certifi CA bundle unavailable, using the system default",
            exc_info=True,
        )
        return ssl.create_default_context()


def _parse_version(version_string):
    # returns an (major, minor, patch) tuple, or None if this isn't a clean
    # released version number (dev builds, anything with a -suffix, etc)
    match = _VERSION_RE.match(version_string.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _combined_notes(newer):
    """The notes of the releases newer than the installed one, as
    [(name, body)] newest first. One release's notes are its own, as written;
    several get a heading each (the release's title) and a rule between them."""
    if len(newer) == 1:
        return newer[0][1]
    return "\n\n---\n\n".join(
        f"# {name}\n\n{body.strip()}".rstrip() for name, body in newer
    )


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
    with urllib.request.urlopen(
        request, timeout=REQUEST_TIMEOUT, context=_ssl_context()
    ) as response:
        payload = json.load(response)

    if not isinstance(payload, list):
        raise ValueError("GitHub's answer wasn't a list of releases")

    # (version, release) for each published release newer than this one
    newer = []
    for release in payload:
        if (
            not isinstance(release, dict)
            or release.get("draft")
            or release.get("prerelease")
        ):
            continue
        version = _parse_version(release.get("tag_name") or "")
        if version is not None and version > current:
            newer.append((version, release))
    if not newer:
        return None
    # GitHub lists by date, not by version
    newer.sort(key=lambda pair: pair[0], reverse=True)
    latest, release = newer[0]
    tag = release["tag_name"]

    return UpdateInfo(
        version=".".join(str(part) for part in latest),
        tag=tag,
        name=release.get("name") or tag,
        html_url=release.get("html_url", f"https://github.com/{REPO}/releases"),
        # "body" is null for a release with no description
        notes=_combined_notes(
            [(r.get("name") or r["tag_name"], r.get("body") or "") for _v, r in newer]
        ),
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
            # already surfaced to the user via failed.emit below - logged
            # too so a report of "update check never works for me" has a
            # trace to actually diagnose (same debug_log convention every
            # other unhandled-exception backstop in this app already uses -
            # see AGENTS.md)
            debug_log.get_logger().error(
                "UpdateCheckWorker: check_for_update failed", exc_info=True
            )
            self.failed.emit(str(exc))
            return
        self.succeeded.emit(info)
