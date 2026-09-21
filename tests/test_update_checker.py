# tests for core/update_checker.py's version comparison and update logic.
# The network call (urllib.request.urlopen) is monkeypatched with a fake
# GitHub API response so these never touch the real network.

import json

import pytest

from core import update_checker
from core.update_checker import UpdateInfo, _parse_version, check_for_update


class _FakeResponse:
    def __init__(self, payload):
        self._data = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._data


def _fake_urlopen(payload):
    def _urlopen(request, timeout=None):
        return _FakeResponse(payload)

    return _urlopen


# --- _parse_version ----------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        ("v0.0.1", (0, 0, 1)),
    ],
)
def test_parse_version_accepts_clean_semver(raw, expected):
    assert _parse_version(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["0.0.0-dev", "v1.2.3-rc1", "1.2", "not-a-version", ""],
)
def test_parse_version_rejects_anything_not_clean_semver(raw):
    assert _parse_version(raw) is None


# --- check_for_update ---------------------------------------------------------


def test_returns_update_info_when_a_newer_release_exists(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen(
            {
                "tag_name": "v1.2.0",
                "name": "AKAISDS 1.2.0",
                "html_url": "https://github.com/martincurkovic/AKAISDS/releases/tag/v1.2.0",
            }
        ),
    )

    result = check_for_update(current_version="1.0.0")

    assert result == UpdateInfo(
        version="1.2.0",
        tag="v1.2.0",
        name="AKAISDS 1.2.0",
        html_url="https://github.com/martincurkovic/AKAISDS/releases/tag/v1.2.0",
    )


def test_returns_none_when_already_on_latest(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen({"tag_name": "v1.0.0", "html_url": "x"}),
    )
    assert check_for_update(current_version="1.0.0") is None


def test_returns_none_when_current_is_newer_than_latest(monkeypatch):
    # can happen if the user is running an unpublished/local build ahead of
    # the last tag - shouldn't ever claim a "downgrade" is an update
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen({"tag_name": "v1.0.0", "html_url": "x"}),
    )
    assert check_for_update(current_version="2.0.0") is None


def test_returns_none_for_dev_build_without_hitting_network(monkeypatch):
    def _explode(request, timeout=None):
        raise AssertionError("dev builds shouldn't make a network call")

    monkeypatch.setattr(update_checker.urllib.request, "urlopen", _explode)
    assert check_for_update(current_version="0.0.0-dev") is None


def test_returns_none_when_latest_tag_is_malformed(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen({"tag_name": "not-a-version", "html_url": "x"}),
    )
    assert check_for_update(current_version="1.0.0") is None
