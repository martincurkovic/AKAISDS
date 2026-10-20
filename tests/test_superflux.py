# core/superflux.py (VelociLoops' SuperFlux onset detector) and the Slice Editor's "Detection" mode that drives it.
# Needs the compiled library (tools/build_velociloops.py); the tests skip without it.

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from core import app_config, rx2, superflux
from ui import slice_editor_window as sew
from ui.slice_editor_window import SliceEditorWindow

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "rx2")
needs_library = pytest.mark.skipif(not rx2.is_available(), reason="VelociLoops not built")


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _loop(name="120Mono"):
    """(mono samples, the loop's own cut points, rate): ReCycle's markers are the answer key."""
    loop = rx2.load(os.path.join(FIXTURES, f"{name}.rx2"))
    samples, truth = loop.mono_buffer()
    return samples, truth, loop.sample_rate


def _matches(found, truth, rate):
    tolerance = int(rate * 0.02)
    return sum(1 for t in truth if any(abs(f - t) <= tolerance for f in found))


def _wait(predicate, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _window(samples, rate, **kwargs):
    return SliceEditorWindow(None, "LOOP", samples, rate, 60, 0, 0, lambda: [], lambda *a, **k: (True, "ok"), **kwargs)


def test_sensitivity_maps_to_a_falling_threshold_and_zero_is_off():
    thresholds = [superflux.threshold_for_sensitivity(s) for s in (1, 25, 50, 75, 100)]
    assert thresholds == sorted(thresholds, reverse=True)
    assert 0.9 < superflux.threshold_for_sensitivity(30) < 1.3  # about VelociLoops' own default (1.1)
    assert superflux.detect(superflux.to_float_buffer([0] * 100), 44100, 0) == []


@needs_library
@pytest.mark.parametrize("name", ["120Mono", "120Stereo"])
def test_superflux_finds_the_cut_points_recycle_made(name):
    samples, truth, rate = _loop(name)
    found = superflux.detect(superflux.to_float_buffer(samples), rate, 40)
    assert _matches(found, truth, rate) == len(truth)
    assert len(found) <= len(truth) + 1  # and not much else


@needs_library
def test_more_sensitivity_never_finds_fewer_hits():
    samples, _truth, rate = _loop()
    buffer = superflux.to_float_buffer(samples)
    counts = [len(superflux.detect(buffer, rate, s)) for s in (10, 50, 100)]
    assert counts == sorted(counts) and counts[-1] > counts[0]


@needs_library
def test_an_unsupported_sample_rate_raises_a_short_error():
    with pytest.raises(rx2.Rx2Error):
        superflux.detect(superflux.to_float_buffer([100, -100] * 500), 1000, 50)


# --- the Slice Editor ---------------------------------------------------------------------------------------------------


def test_the_dialog_offers_both_modes_and_defaults_to_simple(qapp):
    window = _window([0] * 1000, 44100)
    assert window.detection_combo.itemData(0) == app_config.SLICE_DETECTION_SIMPLE
    assert window._detection_mode() == app_config.SLICE_DETECTION_SIMPLE


@needs_library
def test_the_chosen_mode_is_remembered(qapp):
    window = _window([0] * 1000, 44100)
    window.detection_combo.setCurrentIndex(1)
    window._on_detection_mode_chosen(1)
    assert app_config.get_saved_slice_detection() == app_config.SLICE_DETECTION_SUPERFLUX
    assert _window([0] * 1000, 44100)._detection_mode() == app_config.SLICE_DETECTION_SUPERFLUX
    app_config.save_slice_detection(app_config.SLICE_DETECTION_SIMPLE)


def test_superflux_is_greyed_out_when_the_library_is_missing(qapp, monkeypatch):
    monkeypatch.setattr(superflux, "is_available", lambda: False)
    app_config.save_slice_detection(app_config.SLICE_DETECTION_SUPERFLUX)
    window = _window([0] * 1000, 44100)
    assert not window.detection_combo.model().item(1).isEnabled()
    assert window._detection_mode() == app_config.SLICE_DETECTION_SIMPLE
    app_config.save_slice_detection(app_config.SLICE_DETECTION_SIMPLE)


@needs_library
def test_the_slider_in_superflux_mode_places_markers_off_the_gui_thread(qapp):
    samples, truth, rate = _loop()
    window = _window(samples, rate)
    window.detection_combo.setCurrentIndex(1)
    window.transient_sensitivity_slider.setValue(40)
    assert window.waveform.slice_markers() == []  # nothing yet: it waits for a pause, then analyses elsewhere
    assert _wait(lambda: window.waveform.slice_markers())
    assert _matches(window.waveform.slice_markers(), truth, rate) == len(truth)
    assert window.transient_sensitivity_value_label.text() == "40%"
    window._stop_superflux()


@needs_library
def test_only_the_newest_slider_value_is_applied_and_zero_clears(qapp):
    samples, _truth, rate = _loop()
    window = _window(samples, rate)
    window.detection_combo.setCurrentIndex(1)
    window.transient_sensitivity_slider.setValue(100)
    window.transient_sensitivity_slider.setValue(0)  # off again before the first analysis can finish
    assert window.waveform.slice_markers() == []
    QApplication.processEvents()
    time.sleep(0.4)
    QApplication.processEvents()
    assert window.waveform.slice_markers() == []
    window._stop_superflux()


@needs_library
def test_switching_mode_with_the_slider_up_regenerates_with_the_new_detector(qapp):
    samples, truth, rate = _loop()
    window = _window(samples, rate)
    window.transient_sensitivity_slider.setValue(60)  # simple: instant
    simple = window.waveform.slice_markers()
    window.detection_combo.setCurrentIndex(1)
    window._on_detection_mode_chosen(1)
    assert _wait(lambda: window.waveform.slice_markers() != simple)
    assert _matches(window.waveform.slice_markers(), truth, rate) == len(truth)
    window._stop_superflux()
    app_config.save_slice_detection(app_config.SLICE_DETECTION_SIMPLE)


@needs_library
def test_a_failed_analysis_falls_back_to_simple_and_says_so_once(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    monkeypatch.setattr(superflux, "detect", lambda *a: (_ for _ in ()).throw(rx2.Rx2Error("boom")))
    samples, _truth, rate = _loop()
    window = _window(samples, rate)
    window.detection_combo.setCurrentIndex(1)
    window.transient_sensitivity_slider.setValue(60)
    assert _wait(lambda: warnings)
    assert window._detection_mode() == app_config.SLICE_DETECTION_SIMPLE
    assert not window.detection_combo.model().item(1).isEnabled()
    assert window.waveform.slice_markers()  # the simple detector took over
    assert len(warnings) == 1 and "boom" in warnings[0]
    window._stop_superflux()
    app_config.save_slice_detection(app_config.SLICE_DETECTION_SIMPLE)
