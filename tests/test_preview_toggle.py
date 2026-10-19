# ui/preview_toggle.py: the Preview button of the Filter, Time Stretch / Pitch and Reverb dialogs reads Stop while a preview plays and stops it,
# and a knob moved meanwhile does not restart the preview.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QPushButton

from ui.filter_sample_dialog import FilterSampleDialog
from ui.preview_toggle import PreviewToggle
from ui.reverb_dialog import ReverbDialog
from ui.time_stretch_dialog import TimeStretchDialog


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


class FakePlayer(QObject):
    """What SlicePreviewPlayer does to a button: `is_playing`, `stop` and the `finished` signal (which fires however playback ends)."""

    finished = Signal()

    def __init__(self):
        super().__init__()
        self.playing = False
        self.plays = 0

    def is_playing(self):
        return self.playing

    def play(self, *args, **kwargs):
        self.plays += 1
        self.playing = True

    def stop(self):
        was, self.playing = self.playing, False
        if was:
            self.finished.emit()

    def run_out(self):
        self.stop()


def make_toggle():
    button, player = QPushButton("Preview"), FakePlayer()
    toggle = PreviewToggle(button, player, lambda: player.play())
    return button, player, toggle


def test_the_button_starts_a_preview_and_reads_stop_while_it_plays(qapp):
    button, player, _toggle = make_toggle()
    assert button.text() == "Preview"
    button.click()
    assert player.plays == 1 and player.playing and button.text() == "Stop"


def test_clicking_stop_stops_it_and_does_not_start_another(qapp):
    button, player, _toggle = make_toggle()
    button.click()
    button.click()
    assert not player.playing and player.plays == 1 and button.text() == "Preview"


def test_a_preview_that_runs_out_puts_the_button_back(qapp):
    button, player, _toggle = make_toggle()
    button.click()
    player.run_out()
    assert button.text() == "Preview"
    button.click()  # and the next click starts a new one
    assert player.plays == 2 and button.text() == "Stop"


def test_a_preview_that_could_not_start_leaves_the_button_alone(qapp):
    button, player = QPushButton("Preview"), FakePlayer()
    PreviewToggle(button, player, lambda: None)  # (a silent or empty result plays nothing)
    button.click()
    assert button.text() == "Preview"


def test_a_result_that_may_not_start_is_refused_but_a_running_preview_can_still_be_stopped(qapp):
    button, player, toggle = make_toggle()
    toggle.set_available(False)
    assert not button.isEnabled()
    toggle.set_available(True)
    button.click()
    toggle.set_available(False)  # (a knob moved into "too long" while it plays)
    assert button.isEnabled() and button.text() == "Stop"
    button.click()
    assert not player.playing and not button.isEnabled() and button.text() == "Preview"


# -- the three dialogs ------------------------------------------------------------------------------------------------


def _samples():
    return [int(6000 * ((i % 40) / 20 - 1)) for i in range(4410)]


def _filter(qapp):
    dialog = FilterSampleDialog(None, "K", _samples(), 44100)
    return dialog, lambda: dialog.lp_knob.setValue(3000), lambda: dialog.lp_knob.sliderReleased.emit()


def _stretch(qapp):
    dialog = TimeStretchDialog(None, "K", _samples(), 44100)
    return dialog, lambda: dialog.length_knob.setValue(150), lambda: None


def _reverb(qapp):
    dialog = ReverbDialog(None, "K", _samples(), 44100)
    return dialog, lambda: dialog.decay_knob.setValue(80), lambda: None


@pytest.fixture(params=[_filter, _stretch, _reverb], ids=["filter", "time_stretch", "reverb"])
def dialog_with_fake_player(request, qapp, monkeypatch):
    dialog, turn_knob, release_knob = request.param(qapp)
    player = dialog._preview_player
    state = {"playing": False, "plays": 0}

    def play(*args, **kwargs):
        state["plays"] += 1
        state["playing"] = True

    def stop():
        was, state["playing"] = state["playing"], False
        if was:
            player.finished.emit()

    monkeypatch.setattr(player, "play", play)
    monkeypatch.setattr(player, "stop", stop)
    monkeypatch.setattr(player, "is_playing", lambda: state["playing"])
    dialog.state = state
    return dialog, turn_knob, release_knob


def test_each_dialogs_preview_button_becomes_a_stop_button(dialog_with_fake_player):
    dialog, _turn, _release = dialog_with_fake_player
    assert dialog.preview_button.text() == "Preview"
    dialog.preview_button.click()
    assert dialog.state["plays"] == 1 and dialog.preview_button.text() == "Stop"
    dialog.preview_button.click()
    assert not dialog.state["playing"] and dialog.preview_button.text() == "Preview"
    assert dialog.state["plays"] == 1


def test_moving_a_knob_while_a_preview_plays_does_not_restart_or_stop_it(dialog_with_fake_player):
    dialog, turn_knob, release_knob = dialog_with_fake_player
    dialog.preview_button.click()
    turn_knob()
    release_knob()
    QApplication.processEvents()
    assert dialog.state["plays"] == 1 and dialog.state["playing"] and dialog.preview_button.text() == "Stop"


def test_the_next_click_after_a_knob_change_uses_the_new_settings(dialog_with_fake_player):
    dialog, turn_knob, _release = dialog_with_fake_player
    dialog.preview_button.click()
    turn_knob()
    dialog.preview_button.click()  # Stop
    dialog.preview_button.click()  # a fresh preview
    assert dialog.state["plays"] == 2 and dialog.preview_button.text() == "Stop"


def test_closing_a_dialog_stops_its_preview(dialog_with_fake_player):
    dialog, _turn, _release = dialog_with_fake_player
    dialog.preview_button.click()
    dialog.reject()
    assert not dialog.state["playing"]
