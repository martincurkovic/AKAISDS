import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import demo  # noqa: E402


def test_list_shows_every_sampler(capsys):
    assert demo.main(["list"]) == 0
    out = capsys.readouterr().out
    for name in demo.SAMPLERS:
        assert name in out


def test_an_unknown_sampler_is_refused(capsys):
    with pytest.raises(SystemExit):
        demo.main(["nope"])
    assert "unknown sampler" in capsys.readouterr().err


def test_a_flag_that_does_not_apply_is_refused_not_ignored(capsys):
    with pytest.raises(SystemExit):
        demo.main(["ensoniq", "--instant"])
    assert "--instant doesn't apply to ensoniq" in capsys.readouterr().err


def test_every_sampler_has_a_launcher_target():
    for name in demo.SAMPLERS:
        if name not in ("s1000", "s3000"):
            assert os.path.exists(os.path.join(demo.TOOLS, f"{name}_demo.py"))
