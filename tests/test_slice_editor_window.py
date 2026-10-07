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
    program_names=None,
    create_program_result=(True, "program created"),
    create_program_calls=None,
):
    samples = samples if samples is not None else list(range(-5000, 5000))

    def export_callback(*args, **kwargs):
        if export_calls is not None:
            export_calls.append(args)
        return export_result

    def create_program_callback(*args, **kwargs):
        if create_program_calls is not None:
            create_program_calls.append(args)
        return create_program_result

    # program_names=None (the default) omits both the provider and the
    # callback entirely, exactly like dashboard.py's own reuse of this
    # window (nothing resident yet to build a program from) - see this
    # window's own construction comment on why the row isn't built at all
    # in that case, not just disabled
    extra_kwargs = {}
    if program_names is not None:
        extra_kwargs["program_names_provider"] = lambda: list(program_names)
        extra_kwargs["create_program_callback"] = create_program_callback

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
        **extra_kwargs,
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


# --- Sensitivity slider: live transient detection ---------------------------
# not testing the detection algorithm itself here (see tests/
# test_transient_detection.py) - just this window's own wiring: does the
# slider/label agree, does moving it live-regenerate markers with no
# confirmation dialog (unlike Equal Slices), does 0 mean fully off, does it
# still work in demo mode.


def _bursty_samples(total=20000, burst_position=8000, width=200, amplitude=20000):
    samples = [0] * total
    for i in range(burst_position, min(total, burst_position + width)):
        samples[i] = amplitude if (i - burst_position) % 2 == 0 else -amplitude
    return samples


def test_transient_sensitivity_slider_defaults_to_zero_and_label_agrees(qapp):
    window = _build_window(samples=_bursty_samples())
    assert window.transient_sensitivity_slider.value() == 0
    assert window.transient_sensitivity_value_label.text() == "0%"
    assert sew._TRANSIENT_SENSITIVITY_DEFAULT == 0
    window.transient_sensitivity_slider.setValue(90)
    assert window.transient_sensitivity_value_label.text() == "90%"


def test_moving_slider_to_zero_is_fully_off(qapp):
    window = _build_window(samples=_bursty_samples())
    window.transient_sensitivity_slider.setValue(90)
    assert window.waveform.slice_count() >= 2
    window.transient_sensitivity_slider.setValue(0)
    assert window.waveform.slice_markers() == []


def test_moving_slider_above_zero_detects_with_no_confirmation_dialog(qapp, monkeypatch):
    window = _build_window(samples=_bursty_samples())

    def _fail_if_called(*a, **k):
        raise AssertionError("must not prompt while dragging a live slider")

    monkeypatch.setattr(sew.QMessageBox, "question", _fail_if_called)
    window.transient_sensitivity_slider.setValue(90)
    assert window.waveform.slice_count() >= 2


def test_moving_slider_silently_replaces_hand_placed_markers(qapp):
    # no confirmation dialog for this control (see its own construction
    # comment) - touching it away from 0 always wins over whatever was
    # there before, hand-placed or not
    window = _build_window(samples=_bursty_samples())
    window.waveform.set_markers([1000])
    window.transient_sensitivity_slider.setValue(90)
    assert window.waveform.slice_markers() != [1000]


def test_transient_detection_still_works_in_demo_mode(qapp):
    # only Export needs real hardware - marker placement (manual or
    # detected) doesn't
    window = _build_window(samples=_bursty_samples(), demo_mode=True)
    window.transient_sensitivity_slider.setValue(90)
    assert window.waveform.slice_count() >= 2


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


# --- ReCycle-style "export + create program" --------------------------------


def test_create_program_row_not_built_without_a_provider(qapp):
    # dashboard.py's own reuse of this window (slicing a queued local file -
    # nothing resident yet to build a program from) - the row must not
    # exist at all, not just be disabled
    window = _build_window()
    assert window.create_program_checkbox is None
    assert window.template_program_combo is None


def test_create_program_checkbox_present_with_a_provider(qapp):
    window = _build_window(program_names=["Bass stab", "EPiano warm"])
    assert window.create_program_checkbox is not None
    assert window.create_program_checkbox.isEnabled() is True
    assert window.template_program_combo.count() == 2
    assert window.template_program_combo.itemText(0) == "Bass stab"


def test_create_program_checkbox_disabled_in_demo_mode(qapp):
    window = _build_window(program_names=["Bass stab"], demo_mode=True)
    assert window.create_program_checkbox.isEnabled() is False
    assert window.create_program_checkbox.toolTip() != ""


def test_create_program_checkbox_disabled_with_no_resident_programs(qapp):
    window = _build_window(program_names=[])
    assert window.create_program_checkbox.isEnabled() is False
    assert window.create_program_checkbox.toolTip() != ""


def test_create_program_combo_only_enabled_while_checkbox_checked(qapp):
    window = _build_window(program_names=["Bass stab"])
    assert window.template_program_combo.isEnabled() is False
    window.create_program_checkbox.setChecked(True)
    assert window.template_program_combo.isEnabled() is True
    window.create_program_checkbox.setChecked(False)
    assert window.template_program_combo.isEnabled() is False


def test_create_program_combo_only_visible_while_checkbox_checked(qapp):
    # per direct user request: the Template combo doesn't just sit there
    # greyed out - it doesn't appear at all until the checkbox is checked.
    # The bullet separator between the checkbox's own text and "Template:"
    # follows the same show/hide as the label/combo it separates.
    window = _build_window(program_names=["Bass stab"])
    assert window.template_program_combo.isHidden() is True
    assert window._template_program_label.isHidden() is True
    assert window._template_program_separator.isHidden() is True
    window.create_program_checkbox.setChecked(True)
    assert window.template_program_combo.isHidden() is False
    assert window._template_program_label.isHidden() is False
    assert window._template_program_separator.isHidden() is False
    window.create_program_checkbox.setChecked(False)
    assert window.template_program_combo.isHidden() is True
    assert window._template_program_label.isHidden() is True
    assert window._template_program_separator.isHidden() is True


def test_export_row_height_does_not_change_when_template_controls_appear(qapp):
    # regression test: toggling Create New Program used to make the whole
    # export row (and via minimumSizeHint, the whole dialog) grow a few
    # pixels taller the instant the Template combo/label first appeared -
    # see the export_row_container construction comment for why. The
    # container's height must be identical before and after.
    window = _build_window(program_names=["Bass stab"])
    height_before = window._export_row_container.height()
    window.create_program_checkbox.setChecked(True)
    assert window._export_row_container.height() == height_before
    window.create_program_checkbox.setChecked(False)
    assert window._export_row_container.height() == height_before


def test_export_with_create_program_unchecked_does_not_call_the_callback(
    qapp, monkeypatch
):
    create_calls = []
    window = _build_window(
        program_names=["Bass stab"], create_program_calls=create_calls
    )
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)
    window._confirm_export()
    assert create_calls == []


def test_export_with_create_program_checked_calls_the_callback(qapp, monkeypatch):
    export_calls = []
    create_calls = []
    window = _build_window(
        export_calls=export_calls,
        create_program_calls=create_calls,
        program_names=["Bass stab"],
    )
    window.waveform.set_markers([2000])  # -> 2 slices: SQUARE-1, SQUARE-2
    window.create_program_checkbox.setChecked(True)
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)

    window._confirm_export()

    assert len(export_calls) == 1
    assert len(create_calls) == 1
    names, template_index, program_name, _progress, _status = create_calls[0]
    assert names == ["SQUARE-1", "SQUARE-2"]
    assert template_index == 0  # only one template program - "Bass stab"
    assert program_name == "SQUARE"
    assert window.export_succeeded is True


def test_export_refuses_a_program_name_collision(qapp, monkeypatch):
    export_calls = []
    create_calls = []
    window = _build_window(
        export_calls=export_calls,
        create_program_calls=create_calls,
        program_names=["SQUARE", "Bass stab"],
    )
    window.create_program_checkbox.setChecked(True)
    monkeypatch.setattr(sew.QMessageBox, "warning", lambda *a, **k: None)

    window._confirm_export()

    # blocked before even the base sample export starts - same "PDATA
    # silently overwrites a same-named program" hazard
    # ProgramEditorWindow._confirm_duplicate_program already guards against
    assert export_calls == []
    assert create_calls == []


def test_export_more_than_92_slices_with_create_program_is_refused(qapp, monkeypatch):
    export_calls = []
    create_calls = []
    window = _build_window(
        samples=list(range(-200, 30000)),
        export_calls=export_calls,
        create_program_calls=create_calls,
        program_names=["Bass stab"],
    )
    window.create_program_checkbox.setChecked(True)
    markers = sew.sample_slicing.equal_slice_markers(
        window.waveform.start(), window.waveform.end(), 93
    )
    window.waveform.set_markers(markers)
    warnings = []
    monkeypatch.setattr(sew.QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))

    window._confirm_export()

    assert export_calls == []
    assert create_calls == []
    # 93 would put slice 93 on key 129: the ceiling is the keyboard (36..127 = 92 slices), not the 99 keygroups a program can hold
    assert "92" in warnings[0]


def test_exactly_92_slices_with_create_program_are_accepted(qapp, monkeypatch):
    export_calls = []
    window = _build_window(
        samples=list(range(-200, 30000)), export_calls=export_calls, program_names=["Bass stab"],
    )
    window.create_program_checkbox.setChecked(True)
    window.waveform.set_markers(
        sew.sample_slicing.equal_slice_markers(window.waveform.start(), window.waveform.end(), 92)
    )
    monkeypatch.setattr(sew.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)  # (shown, modal, after a successful export)
    window._confirm_export()
    assert len(export_calls) == 1


def test_export_program_creation_failure_marks_the_whole_export_failed(
    qapp, monkeypatch
):
    export_calls = []
    warnings = []
    window = _build_window(
        export_calls=export_calls,
        program_names=["Bass stab"],
        create_program_result=(False, "boom"),
    )
    window.create_program_checkbox.setChecked(True)
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(
        sew.QMessageBox, "warning", lambda *a: warnings.append(a[-1])
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)

    window._confirm_export()

    assert len(export_calls) == 1  # the sample export itself still ran
    assert any("boom" in w for w in warnings)
    assert window.export_succeeded is False


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


def test_export_row_reserves_width_for_every_widget_it_can_ever_show(qapp):
    # real bug this guards: Cancel Transfer/Create Program+Template/the
    # progress bar all becoming visible at once during a real export (with
    # Create Program checked) needed more width than the row - and so the
    # WINDOW - had at rest, so the window visibly grew wider the moment the
    # progress bar appeared (confirmed from a screenshot). export_row_
    # container's own minimum width is reserved up front from every widget
    # it could ever show at once, not just the ones visible right now - the
    # same "reserve the space so nothing has to reflow" principle its own
    # height fix already uses, just applied to width.
    window = _build_window(
        program_names=["Bass stab"], create_program_calls=[],
    )
    reserved_width = window._export_row_container.minimumWidth()

    for widget in (
        window.cancel_export_button,
        window._template_program_separator,
        window._template_program_label,
        window.template_program_combo,
        window.export_progress,
    ):
        if widget is not None:
            widget.setVisible(True)

    assert window._export_row_container.layout().sizeHint().width() <= reserved_width


def test_export_passes_a_busy_callback_that_toggles_the_progress_bar(
    qapp, monkeypatch
):
    # export_callback's own busy_callback kwarg (see ProgramEditorWindow.
    # _export_slices' real use - toggling indeterminate/marquee mode during
    # its own post-send verify, which has no real percentage to report) -
    # this test only checks the plumbing: window._on_export_busy is what's
    # actually passed, and it really does flip export_progress's own range
    received = {}

    def export_callback(*args, busy_callback=None, **kwargs):
        received["busy_callback"] = busy_callback
        return True, "ok"

    window = SliceEditorWindow(
        None, "SQUARE", list(range(-5000, 5000)), 44100, 60, 0, 0,
        lambda: [], export_callback,
    )
    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)

    window._confirm_export()

    busy_callback = received["busy_callback"]
    assert busy_callback == window._on_export_busy
    busy_callback(True)
    assert window.export_progress.minimum() == 0
    assert window.export_progress.maximum() == 0
    busy_callback(False)
    assert window.export_progress.maximum() == 100


def test_status_bar_is_hidden_until_an_export_has_something_to_report(
    qapp, monkeypatch
):
    # a real QStatusBar (style.qss.template's QStatusBar rule), matching
    # the Dashboard/Program Editor's own status bars - the progress bar
    # that used to also live here (and came out invisible against it in
    # real use) has moved to export_row instead (see its own construction
    # comment), so this only ever shows plain text via showMessage(), same
    # as every other status bar in this app. Hidden until there's actually
    # something to show (per direct user request - an always-reserved-but-
    # empty bar read as dead space). Hiding/showing it lets the waveform's
    # own Expanding stretch factor absorb the vertical change instead of
    # resizing the window either way.
    window = _build_window()
    assert window.status_bar.isHidden()

    monkeypatch.setattr(
        sew.QMessageBox, "question", lambda *a, **k: sew.QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)
    window._confirm_export()

    assert not window.status_bar.isHidden()
    assert window.status_bar.currentMessage() != ""


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
    assert window.status_bar.currentMessage() == "it broke"


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
