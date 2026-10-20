# tools/update_velociloops.py: the parts that need no network (the download itself is not tested)

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import update_velociloops as uv

SHA = "f77609ec70b7bb6db362ba97030bd9703539fa01"


def test_the_repo_pins_a_full_commit_sha():
    assert uv.read_version() == SHA


def test_read_version_rejects_junk(tmp_path):
    bad = tmp_path / "VERSION"
    bad.write_text("main\n")
    assert uv.read_version(str(bad)) is None
    assert uv.read_version(str(tmp_path / "missing")) is None


def test_commit_quote_is_replaced_everywhere_it_appears():
    text = "vendored (commit `f77609e`) and again (commit `f77609e`); other `abcdef0` text"
    assert uv.replace_commit_quote(text, "1234567" + "0" * 33) == (
        "vendored (commit `1234567`) and again (commit `1234567`); other `abcdef0` text"
    )


def test_the_docs_that_quote_the_commit_match_the_pinned_one():
    for doc in uv.DOCS_QUOTING_THE_COMMIT:
        with open(os.path.join(uv.ROOT, doc), encoding="utf-8") as f:
            assert f"commit `{SHA[:7]}`" in f.read(), doc


def test_summarize_change():
    assert uv.summarize_change(None, "a\n") == "new file"
    assert uv.summarize_change("a\nb\n", "a\nb\n") == "unchanged"
    assert uv.summarize_change("a\nb\nc\n", "a\nX\nc\nd\n") == "+2 -1 lines"


def test_every_vendored_file_is_listed():
    assert sorted(uv.FILES.values()) == sorted(
        n for n in os.listdir(uv.VENDOR_DIR) if n != "VERSION"
    )


def test_update_writes_files_version_and_doc_quotes(tmp_path, monkeypatch, capsys):
    vendor = tmp_path / "third_party" / "velociloops"
    vendor.mkdir(parents=True)
    for name in uv.FILES.values():
        (vendor / name).write_text("old\n")
    (vendor / "VERSION").write_text(SHA + "\n")
    notices = tmp_path / "src" / "ui" / "help"
    notices.mkdir(parents=True)
    (notices / "THIRD_PARTY_NOTICES.md").write_text("see (commit `f77609e`)\n")
    (tmp_path / "AGENTS.md").write_text("pinned (commit `f77609e`)\n")

    new_sha = "a" * 40
    monkeypatch.setattr(uv, "ROOT", str(tmp_path))
    monkeypatch.setattr(uv, "VENDOR_DIR", str(vendor))
    monkeypatch.setattr(uv, "VERSION_FILE", str(vendor / "VERSION"))
    monkeypatch.setattr(uv, "resolve_ref", lambda ref: new_sha)
    monkeypatch.setattr(uv, "fetch", lambda sha: {n: "new\nlines\n" for n in uv.FILES.values()})
    monkeypatch.setattr(uv, "read_version", lambda path=str(vendor / "VERSION"): SHA)

    assert uv.main(["--no-test"]) == 0

    assert (vendor / "VERSION").read_text() == new_sha + "\n"
    assert (vendor / "velociloops.cpp").read_text() == "new\nlines\n"
    assert (tmp_path / "AGENTS.md").read_text() == "pinned (commit `aaaaaaa`)\n"
    assert "aaaaaaa" in (notices / "THIRD_PARTY_NOTICES.md").read_text()
    assert "+2 -1 lines" in capsys.readouterr().out


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    vendor = tmp_path / "velociloops"
    vendor.mkdir()
    (vendor / "velociloops.cpp").write_text("old\n")
    monkeypatch.setattr(uv, "VENDOR_DIR", str(vendor))
    monkeypatch.setattr(uv, "read_version", lambda path=None: SHA)
    monkeypatch.setattr(uv, "resolve_ref", lambda ref: "b" * 40)
    monkeypatch.setattr(uv, "fetch", lambda sha: {"velociloops.cpp": "new\n"})

    assert uv.main(["--dry-run"]) == 0
    assert (vendor / "velociloops.cpp").read_text() == "old\n"
