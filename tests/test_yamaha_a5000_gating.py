# tests for the A5000 gating: the editor asks the unit for its identity and offers the A5000-only dropdown entries (effects 4-6 as an
# output target) only when the unit says it is an A5000. Real window + session vs FakeA4000 (whose `model` picks the identity reply).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from core import demo_a4000 as demo
from core import yamaha_params as yp
from ui.yamaha_fields import Field, FieldPanel

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_program_editor import build_window, dispose, warning_acknowledged  # noqa: F401  (autouse: no modal warning)

A5000_ONLY = ("Effect 4", "Effect 5", "Effect 6")


def texts(combo):
    return [combo.itemText(i) for i in range(combo.count())]


def open_editor(qapp, model):  # noqa: F811
    fake = demo.FakeA4000(model=model)
    fake.assign(1, "sine wave")
    window = build_window(qapp, fake)
    assert wait_until(lambda: len(window._counts) == 128 and not window._scanning, timeout=20)
    return window


# --- the table ---------------------------------------------------------------------------------------------


def test_the_a5000_only_entries_are_left_out_unless_asked_for():
    for enum in ("output1", "output2", "easy_output1", "easy_output2"):
        shown = [text for _v, text in yp.enum_items(enum)]
        assert not any(name in shown for name in A5000_ONLY) and "Effect 3" in shown
        everything = [text for _v, text in yp.enum_items(enum, a5000=True)]
        assert all(name in everything for name in A5000_ONLY)
        assert [v for v, _t in yp.enum_items(enum, a5000=True)] == sorted(yp.ENUMS[enum])
    assert yp.enum_items("filter_type") == sorted(yp.ENUMS["filter_type"].items())  # other enums are untouched


# --- a panel ------------------------------------------------------------------------------------------------


def test_a_panel_offers_the_extra_outputs_only_once_told_it_is_an_a5000(qapp):  # noqa: F811
    panel = FieldPanel("sample")
    combo = panel.widget(Field("output1", "Output 1", "combo"))
    assert not any(name in texts(combo) for name in A5000_ONLY)
    combo.setCurrentIndex(combo.findData(2))  # Effect 1
    seen = []
    panel.edited.connect(lambda *a: seen.append(a))
    panel.set_a5000(True)
    assert all(name in texts(combo) for name in A5000_ONLY) and combo.currentData() == 2  # the choice survives the rebuild
    panel.set_a5000(False)
    assert not any(name in texts(combo) for name in A5000_ONLY) and combo.currentData() == 2
    assert seen == []  # rebuilding a dropdown is not an edit


def test_a_value_the_unit_holds_that_is_hidden_still_shows(qapp):  # noqa: F811
    panel = FieldPanel("sample")
    combo = panel.widget(Field("output1", "Output 1", "combo"))
    panel.set_a5000(True)
    panel.set_value("output1", 11)  # Effect 5
    assert combo.currentText() == "Effect 5"
    panel.set_a5000(False)
    assert combo.currentData() == 11 and "unexpected" in combo.currentText()  # shown honestly, not silently changed to something else


# --- the window --------------------------------------------------------------------------------------------


def test_an_a4000_is_identified_and_gets_no_effect_4_to_6_outputs(qapp):  # noqa: F811
    window = open_editor(qapp, "A4000")
    try:
        assert wait_until(lambda: window.model == "A4000")
        for panel in (window.program_panel, window.easy_panel, window.samples_tab.panel):
            for key, w in panel.widgets.items():
                if "output" in key and hasattr(w, "itemText"):
                    assert not any(name in texts(w) for name in A5000_ONLY), key
    finally:
        dispose(window)


def test_an_a5000_is_identified_and_gets_them_on_every_page(qapp):  # noqa: F811
    window = open_editor(qapp, "A5000")
    try:
        assert wait_until(lambda: window.model == "A5000")
        combos = 0
        for panel in (window.program_panel, window.easy_panel, window.samples_tab.panel):
            for key, w in panel.widgets.items():
                p = panel.param(key)
                if hasattr(w, "itemText") and p.enum in yp.A5000_ONLY_ENUM_VALUES:
                    combos += 1
                    assert all(name in texts(w) for name in A5000_ONLY), key
        assert combos >= 6  # (program AD in L/R x2, the easy edit pair, the sample pair)
    finally:
        dispose(window)


def test_a_unit_that_does_not_answer_the_identity_request_stays_an_a4000_and_is_asked_again(qapp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(demo.FakeA4000, "_identity", lambda self: None)
    window = open_editor(qapp, "A5000")
    try:
        window._session.identity_timeout_ms = 50
        assert window.model is None
        assert not any(name in texts(window.samples_tab.panel.widgets["output1"]) for name in A5000_ONLY)
        # the editor kept working without it (the programs were read), and the next refresh asks again
        monkeypatch.undo()
        window._refresh()
        assert wait_until(lambda: window.model == "A5000", timeout=10)
    finally:
        dispose(window)


def test_the_identity_reply_is_not_mistaken_for_anything_else_by_the_controller(qapp):  # noqa: F811
    window = open_editor(qapp, "A4000")
    try:
        assert wait_until(lambda: window.model == "A4000")
        statuses = []
        window.controller.status_changed.connect(statuses.append)
        got = []
        window._session.request_identity(got.append)
        assert wait_until(lambda: bool(got), timeout=5)
        assert got[0] is not None and got[0].model == "A4000"
        assert not [s for s in statuses if "nrecognised" in s or "nexpected" in s]
    finally:
        dispose(window)
