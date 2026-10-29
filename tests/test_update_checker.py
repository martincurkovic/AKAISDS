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


def _fake_urlopen(payload, raw=False):
    # GitHub's /releases answers with a list; a single release dict stands for a list of one
    if isinstance(payload, dict) and not raw:
        payload = [payload]

    def _urlopen(request, timeout=None, context=None):
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
                "body": "## What's new\n\n- Something",
            }
        ),
    )

    result = check_for_update(current_version="1.0.0")

    assert result == UpdateInfo(
        version="1.2.0",
        tag="v1.2.0",
        name="AKAISDS 1.2.0",
        html_url="https://github.com/martincurkovic/AKAISDS/releases/tag/v1.2.0",
        notes="## What's new\n\n- Something",
    )


def test_release_with_no_description_has_empty_notes(monkeypatch):
    # GitHub sends "body": null for a release with no description - and
    # older payloads/fakes may omit the key entirely
    for payload in (
        {"tag_name": "v1.2.0", "html_url": "x", "body": None},
        {"tag_name": "v1.2.0", "html_url": "x"},
    ):
        monkeypatch.setattr(
            update_checker.urllib.request, "urlopen", _fake_urlopen(payload)
        )
        assert check_for_update(current_version="1.0.0").notes == ""


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


def test_request_uses_the_certifi_ca_bundle(monkeypatch):
    # a bundled macOS Python has no system CA store; without this the check
    # fails with CERTIFICATE_VERIFY_FAILED (S1100 user's log, 2026-10-16)
    import certifi

    seen = {}

    def fake_context(cafile=None):
        seen["cafile"] = cafile
        return "context"

    def fake_urlopen(request, timeout=None, context=None):
        seen["context"] = context
        return _fake_urlopen({"tag_name": "v1.0.0", "html_url": "x"})(request)

    monkeypatch.setattr(update_checker.ssl, "create_default_context", fake_context)
    monkeypatch.setattr(update_checker.urllib.request, "urlopen", fake_urlopen)

    update_checker.check_for_update(current_version="1.0.0")

    assert seen == {"cafile": certifi.where(), "context": "context"}


def test_falls_back_to_the_default_context_without_certifi(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def no_certifi(name, *args, **kwargs):
        if name == "certifi":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_certifi)

    assert update_checker._ssl_context() is not None


# --- several releases ---------------------------------------------------------


def _release(tag, name=None, body="", **flags):
    return {"tag_name": tag, "name": name or tag, "body": body, "html_url": f"https://x/{tag}", **flags}


def test_every_release_newer_than_the_installed_one_has_its_notes_shown_newest_first(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen(
            [
                _release("v0.3.0", "Version 0.3.0", "- third"),
                _release("v0.2.0", "Version 0.2.0", "- second"),
                _release("v0.1.0", "Version 0.1.0", "- first"),
            ]
        ),
    )
    result = check_for_update(current_version="0.1.0")
    assert (result.version, result.html_url) == ("0.3.0", "https://x/v0.3.0")  # the update is the newest
    assert result.notes == "# Version 0.3.0\n\n- third\n\n---\n\n# Version 0.2.0\n\n- second"  # nothing from 0.1.0 or older


def test_one_newer_release_keeps_its_notes_exactly_as_written(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen([_release("v0.3.0", body="## Notes\n\n- x"), _release("v0.2.0")]),
    )
    assert check_for_update(current_version="0.2.0").notes == "## Notes\n\n- x"


def test_releases_are_ordered_by_version_not_by_the_order_github_lists_them(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen([_release("v1.1.1", "Patch"), _release("v1.2.0", "Minor"), _release("v1.10.0", "Ten")]),
    )
    result = check_for_update(current_version="1.0.0")
    assert result.version == "1.10.0"
    assert result.notes.index("# Ten") < result.notes.index("# Minor") < result.notes.index("# Patch")


def test_drafts_prereleases_and_odd_tags_are_left_out(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen(
            [
                _release("v3.0.0", "Beta", "- beta", prerelease=True),
                _release("v2.5.0", "Draft", "- draft", draft=True),
                _release("nightly", "Nightly", "- n"),
                _release("v1.1.0", "Real", "- real"),
            ]
        ),
    )
    result = check_for_update(current_version="1.0.0")
    assert (result.version, result.notes) == ("1.1.0", "- real")
    monkeypatch.setattr(
        update_checker.urllib.request, "urlopen", _fake_urlopen([_release("v2.0.0", prerelease=True)])
    )
    assert check_for_update(current_version="1.0.0") is None


def test_a_release_with_no_description_still_gets_its_heading(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen([_release("v1.2.0", "Two", None), _release("v1.1.0", "One", "- one")]),
    )
    assert check_for_update(current_version="1.0.0").notes == "# Two\n\n---\n\n# One\n\n- one"


def test_an_error_object_instead_of_a_list_is_a_failure_not_an_update(monkeypatch):
    monkeypatch.setattr(
        update_checker.urllib.request,
        "urlopen",
        _fake_urlopen({"message": "API rate limit exceeded"}, raw=True),
    )
    with pytest.raises(ValueError, match="list of releases"):
        check_for_update(current_version="1.0.0")
