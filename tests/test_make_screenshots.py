"""tools/make_screenshots.py: the tool that regenerates the manual's pictures.

It runs as a subprocess (it sets QT_SCALE_FACTOR and replaces config/log paths for the whole process, which must not leak into this suite)."""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "make_screenshots.py"
QUICKSTART = REPO / "src" / "ui" / "help" / "quickstart.md"


def run_tool(*args, home=None, timeout=180):
    env = {**os.environ}
    env.pop("QT_SCALE_FACTOR", None)
    if home is not None:
        env["HOME"] = str(home)  # (the app's own folders hang off the user's home)
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True, timeout=timeout, env=env, cwd=REPO)


def known_pictures():
    out = run_tool("--list").stdout
    return {line.split()[0] for line in out.splitlines() if line.strip() and not line.startswith(("qt.", "This plugin"))}


def test_every_picture_the_manual_mentions_has_a_recipe():
    mentioned = set(re.findall(r"screenshots/([\w-]+)\.png", QUICKSTART.read_text(encoding="utf-8")))
    mentioned |= set(re.findall(r"SCREENSHOT TODO: ([\w-]+)\.png", QUICKSTART.read_text(encoding="utf-8")))
    assert mentioned, "found no screenshots in quickstart.md"
    missing = sorted(mentioned - known_pictures())
    assert missing == [], f"quickstart.md uses pictures tools/make_screenshots.py can't make: {missing}"


def test_it_makes_pictures_offscreen_at_twice_the_size_and_leaves_the_users_data_alone(tmp_path):
    out, home = tmp_path / "out", tmp_path / "home"
    home.mkdir()
    result = run_tool("--only", "sample-info", "midi-interface-test-results", "--out", str(out), home=home)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 saved, 0 skipped, 0 failed" in result.stdout
    from PySide6.QtGui import QImage

    for name in ("sample-info", "midi-interface-test-results"):
        image = QImage(str(out / f"{name}.png"))
        assert not image.isNull() and image.width() > 300 and image.height() > 200
    assert image.width() % 2 == 0  # 2x: logical sizes are whole numbers of points
    # (importing ui.theme creates an empty "Application Support/AKAISDS" folder, same as starting the app: only files would be a leak)
    assert [p for p in home.rglob("*") if p.is_file()] == [], "the tool wrote files into the user's own folders"


def test_an_existing_picture_is_never_replaced_unless_asked(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    existing = out / "sample-info.png"
    existing.write_bytes(b"a hand-captured screenshot")
    kept = run_tool("--only", "sample-info", "--out", str(out), home=tmp_path)
    assert "skip  sample-info.png" in kept.stdout and "1 skipped" in kept.stdout
    assert existing.read_bytes() == b"a hand-captured screenshot"
    replaced = run_tool("--only", "sample-info.png", "--out", str(out), "--overwrite", home=tmp_path)
    assert "saved sample-info.png" in replaced.stdout
    assert existing.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_an_unknown_name_is_reported_with_the_known_ones(tmp_path):
    result = run_tool("--only", "no-such-picture", "--out", str(tmp_path), home=tmp_path)
    assert result.returncode == 2 and "unknown picture(s): no-such-picture" in result.stdout and "sample-info" in result.stdout


def test_list_says_which_pictures_exist(tmp_path):
    (tmp_path / "sample-info.png").write_bytes(b"x")
    out = run_tool("--list", "--out", str(tmp_path), home=tmp_path).stdout
    rows = {line.split()[0]: line.split()[1] for line in out.splitlines() if " exists " in line or " missing " in line}
    assert rows["sample-info"] == "exists" and rows["main-window-dark"] == "missing"
