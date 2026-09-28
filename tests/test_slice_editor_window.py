# tests for ui/slice_editor_window.py - the Slice Editor dialog itself
# (naming/collision/confirmation UI + the pure export-name math). The real
# hardware-talking export path (ProgramEditorWindow._export_slices) is
# covered in tests/test_program_editor_window.py instead - this file
# fakes export_callback entirely, same reasoning FakeSamplerController
# exists there: this widget's job ends at "call export_callback with the
# right arguments," not at actually talking to a sampler.

import os

# must be set BEFORE the first QApplication() call (below), not just before
# QMessageBox usage - otherwise this file only ever runs offscreen by
# accident, when some OTHER test module happens to get collected first and
# sets this same var process-wide. Standalone (`pytest test_slice_editor_
# window.py`), or first in collection order, it wouldn't be set at all: a
# real QMessageBox (export confirmation, several tests below mock it, but
# the mock only replaces the call - nothing stops the window itself trying
# to show on whatever platform is active) would pop up on a real desktop,
# or QApplication() could fail to construct at all on a genuinely headless
# CI runner with no display server. Same guard several sibling test files
# already use (test_program_editor_window.py, test_knob.py, etc) - this
# file was just missing it.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import ui.slice_editor_window as sew
from ui.slice_editor_window import SliceEditorWindow, slice_export_names


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# --- slice_export_names -----------------------------------------------------


def test_slice_export_names_basic():
    assert slice_export_names("BREAK", 3) == ["BREAK-1", "BREAK-2", "BREAK-3"]


def test_slice_export_names_zero_pads_consistently_for_double_digit_counts():
    names = slice_export_names("BREAK", 12)
    assert names[0] == "BREAK-01"
    assert names[-1] == "BREAK-12"
    # every name shares the same truncated base - not a different width
    # per name (see the function's own docstring)
    assert all(n.startswith("BREAK-") for n in names)


def test_slice_export_names_truncates_long_base_uniformly():
    # NAME_LENGTH is 12; base + "-01".."-16" (suffix width 3) must fit
    names = slice_export_names("BREAKBEATLONGNAME", 16)
    assert all(len(n) <= 12 for n in names)
    assert len(set(names)) == len(names)  # every name still unique
    assert names[0][-3:] == "-01"
    assert names[-1][-3:] == "-16"


def test_slice_export_names_uppercases_and_strips():
    assert slice_export_names("  break  ", 2) == ["BREAK-1", "BREAK-2"]


def test_slice_export_names_are_all_unique_at_the_max_supported_count():
    names = slice_export_names("X", 64)
    assert len(set(names)) == 64


def test_slice_export_names_count_one_skips_the_numeric_suffix():
    # a plain trim/crop (no slice markers) isn't "slice 1 of 1" - see the
    # function's own docstring
    assert slice_export_names("break", 1) == ["BREAK"]


# --- window construction / info label ---------------------------------------


def _build_window(
    samples=None,
    export_result=(True, "ok"),
    existing_names=(),
    export_calls=None,
    demo_mode=False,
):
    samples = samples if samples is not None else list(range(-5000, 5000))

    def export_callback(*args):
        if export_calls is not None:
            export_calls.append(args)
        return export_result

    return SliceEditorWindow(
        None,
        "SQUARE",
        samples,
        44100,
        60,
        0,
        0,
        lambda: list(existing_names),
        export_callback,
        demo_mode=demo_mode,
    )


def test_window_starts_with_one_slice_covering_the_whole_sample(qapp):
    window = _build_window()
    assert window.waveform.slice_count() == 1
    assert "1 slice" in window.info_label.text()


def test_info_label_updates_when_markers_change(qapp):
    window = _build_window()
    window.waveform.set_markers([2000, 5000])
    assert "3 slices" in window.info_label.text()


def test_name_field_defaults_to_the_source_sample_name(qapp):
    window = _build_window()
    assert window.name_edit.text() == "SQUARE"


# --- demo mode: Export disabled, everything else still works ---------------


def test_export_button_enabled_by_default(qapp):
    window = _build_window()
    assert window.export_button.isEnabled() is True


def test_export_button_disabled_in_demo_mode(qapp):
    window = _build_window(demo_mode=True)
    assert window.export_button.isEnabled() is False
    assert window.export_button.toolTip() != ""


def test_marker_placement_and_equal_slices_still_work_in_demo_mode(qapp):
    # only Export needs real hardware - everything else on this dialog
    # doesn't, and must keep working exactly as in the non-demo case
    window = _build_window(demo_mode=True)
    window.equal_count_spin.setValue(4)
    window._generate_equal_slices()
    assert window.waveform.slice_count() == 4


# --- Equal Slices ------------------------------------------------------------


def test_generate_equal_slices_with_no_existing_markers_needs_no_confirmation(qapp):
    window = _build_window()
    window.equal_count_spin.setValue(4)
    window._generate_equal_slices()
    assert window.waveform.slice_count() == 4


def test_generate_equal_slices_asks_before_replacing_existing_markers(qapp, monkeypatch):
    window = _build_window()
    window.waveform.set_markers([1000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.No
    )
    window.equal_count_spin.setValue(4)
    window._generate_equal_slices()
    assert window.waveform.slice_markers() == [1000]  # declined - unchanged


def test_generate_equal_slices_confirmed_replaces_existing_markers(qapp, monkeypatch):
    window = _build_window()
    window.waveform.set_markers([1000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    window.equal_count_spin.setValue(4)
    window._generate_equal_slices()
    assert window.waveform.slice_count() == 4


# --- export guards -----------------------------------------------------------


def test_export_with_no_slice_markers_exports_the_whole_range_as_one_trim(
    qapp, monkeypatch
):
    # per direct user request: doubles as a plain trimmer - exporting with
    # NO slice markers at all sends the whole [start, end] region as ONE
    # sample, not an error case (see SliceEditorWindow's own class
    # docstring)
    calls = []
    samples = list(range(-5000, 5000))
    window = _build_window(samples=samples, export_calls=calls)
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)

    window._confirm_export()

    assert len(calls) == 1
    names, slices, *_ = calls[0]
    # no "-1" suffix for a single trim - see slice_export_names' own
    # comment on why
    assert names == ["SQUARE"]
    assert len(slices) == 1
    assert len(slices[0]) == len(samples)


def test_export_with_empty_name_shows_a_warning_and_does_not_export(qapp, monkeypatch):
    calls = []
    window = _build_window(export_calls=calls)
    window.waveform.set_markers([2000])
    window.name_edit.setText("")
    monkeypatch.setattr(sew.QMessageBox, "warning", lambda *a, **k: None)
    window._confirm_export()
    assert calls == []


def test_export_refuses_a_name_collision(qapp, monkeypatch):
    calls = []
    window = _build_window(
        export_calls=calls, existing_names=["SQUARE-1", "SQUARE-2"]
    )
    window.waveform.set_markers([2000])  # -> 2 slices: SQUARE-1, SQUARE-2
    monkeypatch.setattr(sew.QMessageBox, "warning", lambda *a, **k: None)
    window._confirm_export()
    assert calls == []


def test_export_declined_at_the_confirmation_prompt_does_not_export(qapp, monkeypatch):
    calls = []
    window = _build_window(export_calls=calls)
    window.waveform.set_markers([2000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.No
    )
    window._confirm_export()
    assert calls == []


def test_export_happy_path_calls_export_callback_with_slices_and_metadata(
    qapp, monkeypatch
):
    calls = []
    samples = list(range(-5000, 5000))
    window = _build_window(samples=samples, export_calls=calls)
    window.waveform.set_markers([2000, 6000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)

    window._confirm_export()

    assert len(calls) == 1
    (
        names, slices, framerate, bit_depth, sample_rate,
        spitch, stuno, shlto, progress_cb, status_cb,
    ) = calls[0]
    assert names == ["SQUARE-1", "SQUARE-2", "SQUARE-3"]
    assert len(slices) == 3
    assert sum(len(s) for s in slices) == len(samples)
    assert framerate == 44100
    assert spitch == 60
    assert stuno == 0
    assert shlto == 0
    assert callable(progress_cb) and callable(status_cb)


def test_export_failure_shows_a_warning_not_an_information_dialog(qapp, monkeypatch):
    shown = {}
    window = _build_window(export_result=(False, "it broke"))
    window.waveform.set_markers([2000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(
        sew.QMessageBox, "warning", lambda *a, **k: shown.setdefault("warning", True)
    )
    window._confirm_export()
    assert shown.get("warning") is True
    assert window.status_label.text() == "it broke"


# --- modality guard: ignore Close while an export is in flight --------------


def test_reject_is_ignored_while_exporting(qapp):
    window = _build_window()
    fired = []
    window.rejected.connect(lambda: fired.append(True))
    window._exporting = True
    window.reject()
    assert fired == []
    window._exporting = False
    window.reject()
    assert fired == [True]


# --- close confirmation: unsaved slice edits --------------------------------


def test_window_opens_clean_not_dirty(qapp):
    window = _build_window()
    assert window._dirty is False


def test_placing_a_marker_marks_the_window_dirty(qapp):
    window = _build_window()
    window.waveform.add_marker(len(window._samples) // 2)
    assert window._dirty is True


def test_close_with_no_edits_closes_without_asking(qapp, monkeypatch):
    window = _build_window()
    asked = []
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: asked.append(True)
    )
    fired = []
    window.rejected.connect(lambda: fired.append(True))
    window.reject()
    assert asked == []  # never even asked - nothing to lose
    assert fired == [True]


def test_close_with_unsaved_edits_asks_for_confirmation(qapp, monkeypatch):
    window = _build_window()
    window.waveform.set_markers([2000])
    asked = []

    def fake_question(*a, **k):
        asked.append(True)
        return sew.QMessageBox.StandardButton.No

    monkeypatch.setattr(sew.QMessageBox, "question", fake_question)
    fired = []
    window.rejected.connect(lambda: fired.append(True))
    window.reject()
    assert asked == [True]
    assert fired == []  # declined - window stays open


def test_close_with_unsaved_edits_confirmed_closes(qapp, monkeypatch):
    window = _build_window()
    window.waveform.set_markers([2000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    fired = []
    window.rejected.connect(lambda: fired.append(True))
    window.reject()
    assert fired == [True]


def test_successful_export_clears_the_dirty_flag(qapp, monkeypatch):
    window = _build_window(export_result=(True, "ok"))
    window.waveform.set_markers([2000])
    assert window._dirty is True
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)

    window._confirm_export()

    assert window._dirty is False
    # closing right after a successful export needs no confirmation
    asked = []
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: asked.append(True)
    )
    fired = []
    window.rejected.connect(lambda: fired.append(True))
    window.reject()
    assert asked == []
    assert fired == [True]


def test_failed_export_does_not_clear_the_dirty_flag(qapp, monkeypatch):
    window = _build_window(export_result=(False, "it broke"))
    window.waveform.set_markers([2000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "warning", lambda *a, **k: None)

    window._confirm_export()

    assert window._dirty is True


def test_export_succeeded_flag_starts_false_and_stays_false_on_failure(
    qapp, monkeypatch
):
    # ProgramEditorWindow._open_slice_editor reads this after .exec() to
    # decide whether a sample-list reload is even needed - see its own
    # comment on why an unconditional reload was the actual cause of the
    # waveform disappearing after closing this dialog
    window = _build_window(export_result=(False, "it broke"))
    assert window.export_succeeded is False
    window.waveform.set_markers([2000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "warning", lambda *a, **k: None)

    window._confirm_export()

    assert window.export_succeeded is False


def test_export_succeeded_flag_set_true_on_a_successful_export(qapp, monkeypatch):
    window = _build_window(export_result=(True, "ok"))
    window.waveform.set_markers([2000])
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)

    window._confirm_export()

    assert window.export_succeeded is True


# --- zoom row: terse +/- labels, Fit collapses the scrollbar ----------------


def test_zoom_buttons_use_terse_symbols_not_the_word_zoom(qapp):
    window = _build_window()
    assert window.zoom_out_button.text() == "−"
    assert window.zoom_in_button.text() == "+"


def test_zoom_in_out_buttons_match_the_samples_tabs_own_width(qapp):
    # same 36px as program_editor_window.py's own zoom_out_button/
    # zoom_in_button on the Samples tab - both windows' zoom controls
    # should be pixel-identical, not each pick their own width
    window = _build_window()
    assert window.zoom_out_button.width() == 36
    assert window.zoom_in_button.width() == 36


def test_scrollbar_is_hidden_when_fully_zoomed_out(qapp):
    window = _build_window(samples=list(range(-5000, 5000)))
    assert window.scrollbar.isHidden()


def test_scrollbar_appears_once_zoomed_in_and_hides_again_on_fit(qapp):
    window = _build_window(samples=list(range(-100000, 100000)))
    window.waveform.zoom_in()
    window.waveform.zoom_in()
    assert not window.scrollbar.isHidden()
    window.zoom_fit_button.click()
    assert window.scrollbar.isHidden()
