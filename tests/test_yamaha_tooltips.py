# tests for ui/yamaha_tooltips.py: every parameter row gets a readable tooltip (tier 1), the written sentences exist for the common controls
# (tier 2), and the widgets use them.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re

import pytest

from core import yamaha_params as yp
from ui import yamaha_tooltips as yt
from ui.yamaha_fields import Field, FieldPanel

from test_s950_transfers import qapp  # noqa: F401


def all_rows():
    return [r for scope in yp.SCOPES for r in yp.rows(scope) if r.kind == "int"]


def test_every_row_gets_a_clean_tooltip():
    for row in all_rows():
        lines = yt.tooltip(row).split("\n")
        assert lines[0][:1].isupper(), row.key  # sentence case, not "program LFO ..."
        text = "\n".join(lines)
        assert not re.search(r"[A-Za-z]\d", lines[0]), (row.key, lines[0])  # value3 / output1 are split: "value 3"
        assert not re.search(r"\b(AEG|FEG|PEG|AD in)\b", text), (row.key, text)  # abbreviations are spelled out
        assert all(len(line) <= 100 for line in lines), (row.key, lines)  # short lines
        if row.enum:
            assert not any(line.startswith("Range") for line in lines), row.key  # a dropdown shows its options instead
        else:
            assert any(line.startswith("Range: ") for line in lines), row.key


def test_the_names_become_titles():
    h = yt.humanise
    assert h("AEG attack rate") == "Amplitude envelope attack rate"
    assert h("program LFO step wave value3") == "Program LFO step wave value 3"
    assert h("output1 level") == "Output 1 level"
    assert h("key x-fade on (-1 =sample)") == "Key crossfade on"
    assert h("pan (-64 = random)") == "Pan"
    assert h("AD in R output2") == "Audio input R output 2"
    assert h("loop tempo (x100, 80.00-159.99)") == "Loop tempo"


def test_special_values_go_on_the_range_line():
    assert "Range: -64 to 63 (-64 = random)" in yt.tooltip(yp.get("sample", "pan"))
    assert "Range: -1 to 127 (-1 = original)" in yt.tooltip(yp.get("sample", "key_range_low"))
    assert "Range: 80.00 to 159.99" in yt.tooltip(yp.get("sample", "loop_tempo"))  # stored x100, shown as the panel shows it
    assert "(64-68 = random 1-5)" in yt.tooltip(yp.get("sample", "cutoff_velocity_sensitivity"))


def test_written_sentences_cover_the_common_controls_and_the_rules_cover_the_families():
    t = yt.tooltip
    assert "Higher is faster" in t(yp.get("sample", "aeg_attack_rate"))
    assert "filter envelope" in t(yp.get("sample", "feg_sustain_level"))
    assert "pitch envelope" in t(yp.get("sample", "peg_range"))
    assert "Level of step 7" in t(yp.get("program", "lfo_step_value_7"))
    assert "for this program only" in t(yp.get("easy_edit", "pan_offset"))
    assert "control 4" in t(yp.get("sample", "control4_range"))
    assert t(yp.get("sample", "original_key_l")).split("\n")[1] == "The key at which the sample plays at its recorded pitch."


def test_widgets_use_them_unless_a_field_brings_its_own(qapp):  # noqa: F811
    panel = FieldPanel("sample")
    knob = panel.widget(Field("aeg_attack_rate", "Attack", "knob"))
    assert knob.toolTip() == yt.tooltip(yp.get("sample", "aeg_attack_rate"))
    own = FieldPanel("sample").widget(Field("pan", "Pan", "knob", tip="my own"))
    assert own.toolTip() == "my own"
