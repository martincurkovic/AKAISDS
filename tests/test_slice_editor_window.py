# tests for ui/slice_editor_window.py - the Slice Editor dialog itself
# (naming/collision/confirmation UI + the pure export-name math). The real
# hardware-talking export path (ProgramEditorWindow._export_slices) is
# covered in tests/test_program_editor_window.py instead - this file
# fakes export_callback entirely, same reasoning FakeSamplerController
# exists there: this widget's job ends at "call export_callback with the
# right arguments," not at actually talking to a sampler.

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


# --- window construction / info label ---------------------------------------


def _build_window(
    samples=None,
    export_result=(True, "ok"),
    existing_names=(),
    export_calls=None,
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


def test_export_with_no_slice_markers_shows_a_message_and_does_not_export(
    qapp, monkeypatch
):
    calls = []
    window = _build_window(export_calls=calls)
    monkeypatch.setattr(sew.QMessageBox, "information", lambda *a, **k: None)
    window._confirm_export()
    assert calls == []


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
