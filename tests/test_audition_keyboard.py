# The on-screen keyboard (Cmd/Ctrl+K in the Program Editor): the messages, the keyboard widget, the bar, the BridgeWorker's silent midi job and the window's wiring.
# Behaviour is meant to match JV-Studio's audition bar (minus its patch browser and Master slider).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

from s3ked.demo import DemoBridge

from core import audition
from core.program_editor_bridge import BridgeWorker
from ui.audition_bar import AuditionBar
from ui.keyboard import FIRST_NOTE, LAST_NOTE, PianoKeyboard
from ui.program_editor_window import ProgramEditorWindow


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


# -- the messages ---------------------------------------------------------------------------------------------------------


def test_notes_and_panic_are_plain_channel_messages():
    assert audition.note_on(0, 60, 100) == bytes([0x90, 60, 100]) and audition.note_off(15, 61) == bytes([0x8F, 61, 0])
    assert audition.note_on(2, 60, 0)[2] == 1  # (velocity 0 would be a note-off)
    assert audition.panic(3) == [bytes([0xB3, 64, 0]), bytes([0xB3, 120, 0]), bytes([0xB3, 123, 0])]
    with pytest.raises(ValueError):
        audition.note_on(16, 60)
    with pytest.raises(ValueError):
        audition.note_on(0, 128)


def test_velocity_steps_snap_to_the_nearest_multiple_of_ten_then_move_one():
    assert audition.step_velocity(97, 1) == 110 and audition.step_velocity(97, -1) == 90
    assert audition.step_velocity(100, 1) == 110 and audition.step_velocity(100, -1) == 90
    assert audition.step_velocity(127, 1) == 127 and audition.step_velocity(1, -1) == 1
    assert audition.step_velocity(125, -1) == 120 and audition.step_velocity(5, 1) == 10


# -- the keyboard widget --------------------------------------------------------------------------------------------------


@pytest.fixture
def keys(qapp):
    keyboard = PianoKeyboard()
    keyboard.resize(1200, 82)
    played = []
    keyboard.note_on.connect(lambda n: played.append(("on", n)))
    keyboard.note_off.connect(lambda n: played.append(("off", n)))
    keyboard.played = played
    keyboard.show()
    yield keyboard
    keyboard.release_all()
    keyboard.deleteLater()


def key_event(kind, key, text="", modifiers=Qt.KeyboardModifier.NoModifier):
    return QKeyEvent(kind, key, modifiers, text)


def test_the_computer_keys_play_from_middle_c_and_z_x_change_the_octave(keys):
    keys.keyPressEvent(key_event(QEvent.Type.KeyPress, Qt.Key.Key_A, "a"))
    keys.keyPressEvent(key_event(QEvent.Type.KeyPress, Qt.Key.Key_W, "w"))
    keys.keyReleaseEvent(key_event(QEvent.Type.KeyRelease, Qt.Key.Key_A, "a"))
    keys.keyReleaseEvent(key_event(QEvent.Type.KeyRelease, Qt.Key.Key_W, "w"))
    assert keys.played == [("on", 60), ("on", 61), ("off", 60), ("off", 61)]
    keys.keyPressEvent(key_event(QEvent.Type.KeyPress, Qt.Key.Key_Z, "z"))
    assert keys.typing_base == 48
    keys.keyPressEvent(key_event(QEvent.Type.KeyPress, Qt.Key.Key_X, "x"))
    keys.keyPressEvent(key_event(QEvent.Type.KeyPress, Qt.Key.Key_X, "x"))
    assert keys.typing_base == 72


def test_the_range_covers_the_akai_notes_and_a_black_key_sits_above_its_white_key(keys):
    assert (FIRST_NOTE, LAST_NOTE) == (24, 108)
    assert keys.note_at(1, 5) == FIRST_NOTE and keys.note_at(keys.width() - 2, 5) == LAST_NOTE
    black = keys._key_rect(61).center()
    assert keys.note_at(black.x(), black.y()) == 61


def test_clicking_plays_and_dragging_is_a_glissando(keys):
    rect = keys._key_rect(60)
    QTest.mousePress(keys, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert keys.played == [("on", 60)]
    QTest.mouseRelease(keys, Qt.MouseButton.LeftButton, pos=rect.center().toPoint())
    assert keys.played == [("on", 60), ("off", 60)]


def test_losing_focus_or_being_disabled_releases_everything(keys):
    keys.keyPressEvent(key_event(QEvent.Type.KeyPress, Qt.Key.Key_A, "a"))
    keys.setEnabled(False)
    assert keys.played == [("on", 60), ("off", 60)]


def test_the_patch_and_bank_keys_of_jv_studio_do_not_exist_here(keys):
    # a sampler has no banks: `,` `.` `[` `]` are swallowed by the bar but play nothing and send nothing
    for key, text in ((Qt.Key.Key_Comma, ","), (Qt.Key.Key_Period, "."), (Qt.Key.Key_BracketLeft, "["), (Qt.Key.Key_BracketRight, "]")):
        keys.keyPressEvent(key_event(QEvent.Type.KeyPress, key, text))
    assert keys.played == []


# -- the bar --------------------------------------------------------------------------------------------------------------


@pytest.fixture
def bar(qapp):
    bar = AuditionBar()
    bar.resize(1300, 200)
    sent = []
    bar.midi_out.connect(sent.append)
    bar.sent = sent
    bar.show()
    bar.set_available(True)
    bar.set_open(True)
    yield bar
    bar.set_open(False)
    bar.deleteLater()


def test_the_bar_always_starts_closed_because_an_open_one_owns_the_keyboard(qapp):
    fresh = AuditionBar()
    assert not fresh.is_open() and not fresh.routing_keys()
    fresh.deleteLater()


def test_notes_use_the_channel_and_velocity_and_the_off_follows_the_channel_it_started_on(bar):
    bar.channel_combo.setCurrentIndex(2)
    bar.velocity.setValue(80)
    bar.keyboard._press(60)
    bar.channel_combo.setCurrentIndex(5)
    bar.keyboard._release(60)
    assert bar.sent == [audition.note_on(2, 60, 80), audition.note_off(2, 60)]


def test_panic_releases_and_silences_the_channel(bar):
    bar.channel_combo.setCurrentIndex(3)
    bar.keyboard._press(60)
    bar.sent.clear()
    bar.panic()
    assert bar.sent == [audition.note_off(3, 60), *audition.panic(3)]


def test_an_unavailable_bar_plays_nothing_and_lets_go_of_what_was_held(bar):
    bar.keyboard._press(60)
    bar.sent.clear()
    bar.set_available(False, "The keyboard is off while the sampler is busy.")
    assert bar.sent == [audition.note_off(0, 60)]
    assert not bar.routing_keys() and not bar.body.isEnabled()
    assert "busy" in bar.note.text()
    bar.sent.clear()
    bar.keyboard._press(62)
    assert bar.sent == []


def test_c_and_v_snap_the_velocity_to_a_multiple_of_ten_then_step(bar):
    bar.velocity.setValue(97)
    bar.keyboard.keyPressEvent(key_event(QEvent.Type.KeyPress, Qt.Key.Key_V, "v"))
    assert bar.velocity.value() == 110
    bar.velocity.setValue(97)
    bar.keyboard.keyPressEvent(key_event(QEvent.Type.KeyPress, Qt.Key.Key_C, "c"))
    assert bar.velocity.value() == 90


def test_the_notes_being_played_are_shown_live_with_the_akai_note_names(bar):
    bar.keyboard._press(60)
    bar.keyboard._press(64)
    assert bar.playing_label.text() == "C3   E3"  # (the S3000 panel calls note 60 C3)
    bar.keyboard._release(60)
    bar.keyboard._release(64)
    assert bar.playing_label.text() == ""
    assert bar.typing_label.text() == "Octave: C3 to E4"


@pytest.fixture
def routed(bar, monkeypatch):
    monkeypatch.setattr(bar, "_window_in_front", lambda: True)  # (an offscreen window is never active)
    return bar


def test_while_open_every_ordinary_key_goes_to_the_piano_not_to_a_text_box(routed):
    assert routed.eventFilter(QWidget(), key_event(QEvent.Type.KeyPress, Qt.Key.Key_A, "a")) is True
    assert routed.eventFilter(QWidget(), key_event(QEvent.Type.KeyRelease, Qt.Key.Key_A, "a")) is True
    assert routed.sent == [audition.note_on(0, 60, 100), audition.note_off(0, 60)]
    assert routed.eventFilter(QWidget(), key_event(QEvent.Type.KeyPress, Qt.Key.Key_Q, "q")) is True  # (swallowed even when it plays nothing)


def test_shortcuts_and_navigation_keys_still_work(routed):
    cmd = Qt.KeyboardModifier.ControlModifier  # (Cmd on a Mac)
    assert routed.eventFilter(QWidget(), key_event(QEvent.Type.KeyPress, Qt.Key.Key_K, "k", cmd)) is False
    for key in (Qt.Key.Key_Tab, Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Left, Qt.Key.Key_Space, Qt.Key.Key_F5):
        assert routed.eventFilter(QWidget(), key_event(QEvent.Type.KeyPress, key)) is False
    assert routed.sent == []


def test_the_keys_are_given_back_when_the_bar_closes_it_is_unavailable_or_another_window_is_in_front(routed, monkeypatch):
    press = key_event(QEvent.Type.KeyPress, Qt.Key.Key_A, "a")
    monkeypatch.setattr(routed, "_window_in_front", lambda: False)
    assert routed.eventFilter(QWidget(), press) is False
    monkeypatch.setattr(routed, "_window_in_front", lambda: True)
    routed.set_available(False, "busy")
    assert routed.eventFilter(QWidget(), press) is False
    routed.set_available(True)
    routed.set_open(False)
    assert routed.eventFilter(QWidget(), press) is False


def test_hiding_the_bar_lets_go_of_held_notes(bar):
    bar.keyboard._press(60)
    bar.sent.clear()
    bar.set_open(False)
    assert bar.sent == [audition.note_off(0, 60)]


# -- the BridgeWorker's silent midi job -----------------------------------------------------------------------------------


class _Out:
    def __init__(self, fail=False):
        self.messages = []
        self.fail = fail

    def send_message(self, message):
        if self.fail:
            raise OSError("port closed")
        self.messages.append(list(message))


class _OutBridge:
    def __init__(self, out):
        self.out = out


def test_a_midi_job_sends_the_bytes_on_the_bridges_port_without_lighting_the_busy_bar():
    out = _Out()
    worker = BridgeWorker(_OutBridge(out))
    busy = []
    worker.busy_changed.connect(busy.append)
    worker.submit_midi(audition.note_on(1, 60, 90))
    worker.submit_midi(audition.note_off(1, 60))
    worker.process_pending()
    assert out.messages == [[0x91, 60, 90], [0x81, 60, 0]] and busy == []


def test_a_real_job_around_a_midi_job_still_announces_busy_once_and_idle_once():
    worker = BridgeWorker(_OutBridge(_Out()))
    busy = []
    worker.busy_changed.connect(busy.append)
    worker.submit_midi(audition.note_on(0, 60))
    worker.submit_program_list()  # (the bridge has no program_list: the handler reports the failure, which is all this needs)
    worker.submit_midi(audition.note_off(0, 60))
    worker.process_pending()
    assert busy == [True, False]
    assert worker.is_idle()


def test_a_failed_send_is_reported_not_raised():
    worker = BridgeWorker(_OutBridge(_Out(fail=True)))
    failures = []
    worker.midi_send_failed.connect(failures.append)
    worker.submit_midi(audition.note_on(0, 60))
    worker.process_pending()
    assert failures == ["port closed"]


def test_the_demo_bridge_has_no_port_so_a_note_is_silently_dropped():
    worker = BridgeWorker(DemoBridge())
    failures = []
    worker.midi_send_failed.connect(failures.append)
    worker.submit_midi(audition.note_on(0, 60))
    worker.process_pending()
    assert failures == []


# -- the window -----------------------------------------------------------------------------------------------------------


class _WithOut:
    """The demo bridge plus a MIDI port that records what the keyboard sends."""

    def __init__(self):
        self._demo = DemoBridge()
        self.out = _Out()

    def __getattr__(self, name):
        return getattr(self._demo, name)


@pytest.fixture
def win(qapp):
    bridge = _WithOut()
    window = ProgramEditorWindow(QWidget(), bridge=bridge)
    window._worker.wait_until_idle()
    window.bridge_out = bridge.out
    yield window
    window.audition_bar.set_open(False)
    window._worker.stop()
    window._worker.wait()
    window.deleteLater()


def test_cmd_k_shows_and_hides_the_keyboard_and_the_menu_item_follows(win):
    assert win.audition_action.shortcut().toString() == "Ctrl+K" and win.audition_action.isCheckable()
    assert not win.audition_bar.is_open() and not win.audition_action.isChecked()
    win.audition_action.trigger()
    assert win.audition_bar.is_open() and win.audition_action.isChecked()
    win.audition_action.trigger()
    assert not win.audition_bar.is_open() and not win.audition_action.isChecked()
    win.audition_bar.toggle.click()  # (the bar's own button too)
    assert win.audition_action.isChecked()


def test_a_key_press_reaches_the_samplers_port_through_the_worker(win, qapp):
    win.audition_bar.set_open(True)
    win.audition_bar.keyboard._press(60)
    win.audition_bar.keyboard._release(60)
    win._worker.wait_until_idle()
    assert win.bridge_out.messages == [[0x90, 60, 100], [0x80, 60, 0]]


def test_the_keyboard_is_off_during_a_blocking_hardware_operation(win):
    win._set_hardware_busy_ui(True)
    assert not win.audition_bar.body.isEnabled() and "busy" in win.audition_bar.note.text()
    win.audition_bar.keyboard._press(60)
    win._worker.wait_until_idle()
    assert win.bridge_out.messages == []
    win._set_hardware_busy_ui(False)
    assert win.audition_bar.body.isEnabled()


def test_closing_the_window_gives_the_computer_keyboard_back(win):
    win.audition_bar.set_open(True)
    win.close()
    assert not win.audition_bar.is_open()


def test_a_note_does_not_light_the_loading_bar(win, qapp):
    for _ in range(5):  # (let the start-up load chain finish: it submits its next job from the previous one's result)
        win._worker.wait_until_idle()
        qapp.processEvents()
    win._busy_show_timer.stop()
    win._busy_hide_timer.stop()
    busy = []
    win._worker.busy_changed.connect(busy.append)
    win.audition_bar.set_open(True)
    win.audition_bar.keyboard._press(60)
    win.audition_bar.keyboard._release(60)
    win._worker.wait_until_idle()
    qapp.processEvents()
    assert win.bridge_out.messages == [[0x90, 60, 100], [0x80, 60, 0]]
    assert busy == [] and not win._busy_show_timer.isActive()


# -- locking the keyboard during a long job -----------------------------------------------------------------------------


def test_a_locked_bar_closes_and_cannot_be_opened(bar):
    opened, locks = [], []
    bar.open_changed.connect(opened.append)
    bar.locked_changed.connect(locks.append)
    bar.set_available(True)
    bar.set_open(True)
    bar.set_locked(True, "busy")
    assert not bar.is_open() and bar.is_locked() and not bar.toggle.isEnabled()
    assert opened[-1] is False and locks == [True]  # (it was shown, then closed by the lock)
    bar.set_open(True)  # (the shortcut, the View menu item, the bar's own button)
    assert not bar.is_open()
    bar.toggle.click()
    assert not bar.is_open()
    bar.set_locked(True, "busy")  # (cheap to repeat: no second signal)
    assert locks == [True]


def test_unlocking_leaves_the_bar_closed_and_lets_it_be_opened_again(bar):
    bar.set_available(True)
    bar.set_open(True)
    bar.set_locked(True)
    bar.set_locked(False)
    assert not bar.is_open() and bar.toggle.isEnabled()
    bar.set_open(True)
    assert bar.is_open()


def test_a_blocking_job_closes_the_keyboard_and_greys_out_its_menu_item(win):
    win.audition_bar.set_open(True)
    assert win.audition_bar.is_open() and win.audition_action.isEnabled()
    win._set_hardware_busy_ui(True)
    assert not win.audition_bar.is_open() and not win.audition_action.isEnabled()
    win.audition_action.trigger()  # (Ctrl+K is a shortcut of the same action, which is disabled)
    win.audition_bar.set_open(True, focus=True)
    assert not win.audition_bar.is_open()
    win._set_hardware_busy_ui(False)
    assert win.audition_action.isEnabled() and not win.audition_bar.is_open()  # (not reopened for them)
    win.audition_action.trigger()
    assert win.audition_bar.is_open()
