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


# -- the Space bar ----------------------------------------------------------------------------------------------------


def test_space_toggles_the_preview_from_the_dialog(dialog_with_fake_player):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dialog, _turn, _release = dialog_with_fake_player
    QTest.keyClick(dialog, Qt.Key.Key_Space)
    assert dialog.state["playing"] and dialog.preview_button.text() == "Stop"
    QTest.keyClick(dialog, Qt.Key.Key_Space)
    assert not dialog.state["playing"] and dialog.preview_button.text() == "Preview"
    assert dialog.state["plays"] == 1


def test_space_on_a_focused_knob_toggles_the_preview(dialog_with_fake_player):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dialog, _turn, _release = dialog_with_fake_player
    knob = next(k for k in dialog.findChildren(__import__("ui.knob", fromlist=["Knob"]).Knob) if k.isEnabled() and k.isVisibleTo(dialog))
    QTest.keyClick(knob, Qt.Key.Key_Space)  # (the knob does not use Space, so it goes on up to the dialog)
    assert dialog.state["playing"]
    if hasattr(dialog, "lp_group"):
        assert dialog.lp_group.isChecked()  # the check box of the group around the knob did not take the Space and flip the filter's bypass
    QTest.keyClick(knob, Qt.Key.Key_Space)
    assert not dialog.state["playing"]


def test_space_on_a_focused_ok_button_still_presses_ok(dialog_with_fake_player):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QDialogButtonBox

    dialog, turn_knob, _release = dialog_with_fake_player
    turn_knob()  # (Time Stretch has nothing to apply at 100 %: OK is off until something is changed)
    ok = dialog._button_box.button(QDialogButtonBox.StandardButton.Ok)
    assert ok.isEnabled()
    QTest.keyClick(ok, Qt.Key.Key_Space)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.state["plays"] == 0  # the preview was not started by it


def test_other_keys_and_modified_space_do_nothing(dialog_with_fake_player):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dialog, _turn, _release = dialog_with_fake_player
    QTest.keyClick(dialog, Qt.Key.Key_P)
    QTest.keyClick(dialog, Qt.Key.Key_Space, Qt.KeyboardModifier.ControlModifier)
    assert dialog.state["plays"] == 0


def test_space_does_nothing_while_a_preview_may_not_start(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    button, player = QPushButton("Preview"), FakePlayer()
    toggle = PreviewToggle(button, player, lambda: player.play())
    toggle.set_available(False)

    class Event:
        def key(self):
            return Qt.Key.Key_Space

        def modifiers(self):
            return Qt.KeyboardModifier.NoModifier

    assert toggle.handle_key(Event()) is False and player.plays == 0


def test_a_held_space_does_not_flicker_the_preview(qapp):
    from PySide6.QtCore import Qt

    button, player, toggle = make_toggle()

    class Event:
        repeating = False
        accepted = False

        def key(self):
            return Qt.Key.Key_Space

        def modifiers(self):
            return Qt.KeyboardModifier.NoModifier

        def isAutoRepeat(self):
            return self.repeating

        def accept(self):
            self.accepted = True

    first = Event()
    assert toggle.handle_key(first) and player.playing and first.accepted
    held = Event()
    held.repeating = True
    assert toggle.handle_key(held) and player.playing  # still playing: the repeat was swallowed, not toggled


# -- Space with the focus on the dialog's first control ---------------------------------------------------------------


def test_space_on_the_first_focused_control_toggles_the_preview_not_the_control(dialog_with_fake_player):
    """A dialog opens with the focus on its first control (the Filter's highpass check box, the others' first combo): Space must not toggle that."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QComboBox, QGroupBox

    dialog, _turn, _release = dialog_with_fake_player
    if hasattr(dialog, "hp_group"):
        control = dialog.hp_group
        before = control.isChecked()
        QTest.keyClick(control, Qt.Key.Key_Space)
        assert control.isChecked() == before
    else:
        control = dialog.findChildren(QComboBox)[0]
        before = control.currentIndex()
        QTest.keyClick(control, Qt.Key.Key_Space)
        assert control.currentIndex() == before and not control.view().isVisible()
    assert dialog.state["playing"] and dialog.preview_button.text() == "Stop"
    QTest.keyClick(control, Qt.Key.Key_Space)
    assert not dialog.state["playing"]


def test_every_checkbox_group_box_and_combo_claims_space(dialog_with_fake_player):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QCheckBox, QComboBox, QGroupBox

    dialog, _turn, _release = dialog_with_fake_player
    for kind in (QComboBox, QCheckBox, QGroupBox):
        for control in dialog.findChildren(kind):
            if not control.isEnabled():
                continue
            QTest.keyClick(control, Qt.Key.Key_Space)
            assert dialog.state["playing"], (kind.__name__, control)
            dialog._preview_player.stop()
