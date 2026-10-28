"""The keyboard bar at the bottom of the Program Editor: a piano to hear the sampler. Same behaviour as JV-Studio's audition bar, minus its patch browser and Master slider.

Nothing here writes to the sampler. It only emits what to send (`midi_out`, one complete channel message at a time); the window decides whether it is safe to send.

While the bar is OPEN (and available) it owns the computer keyboard, as Ableton's computer MIDI keyboard does: every key press that is not a shortcut (Ctrl/Cmd/Alt) or a
navigation key (Tab, arrows, Esc, Enter, F-keys) is routed to the piano, whatever has the focus, so nothing can be typed into a text box. It stands aside while another window is
in front (a dialog, the Slice Editor) or a drop-down is open. Cmd+K closes the bar and gives the keys back. The bar always starts closed.

The Input drop-down forwards a second MIDI input to the sampler through the same `midi_out` (a keyboard, or a loopback port a DAW plays into, for an interface whose driver lets only one
program use it). Only notes, pitch bend and a few controllers get through (`audition.from_input`), on the channel they arrived on. The port is open only while the bar is open and available, so
closing the bar gives the device back, and the sampler's own ports are never offered (a port echoing back into itself is how the 2026-10-10 incident started).
"""

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import app_config, audition, debug_log, midi_transport
from core.midi_notes import midi_note_to_name
from ui.keyboard import TYPING_KEYS, PianoKeyboard, ScrollableKeyboard

HELP = (
    "Play the sampler from the keyboard below.\n"
    "While open, the computer keyboard keys A-L play notes, Z and X change the octave, C and V the velocity, [ and ] the MIDI channel. "
    "Cmd+K to show or hide keyboard."
)

#: keys that keep working while the bar owns the keyboard: moving around, confirming, cancelling
_PASS_THROUGH = {
    Qt.Key.Key_Tab,
    Qt.Key.Key_Backtab,
    Qt.Key.Key_Escape,
    Qt.Key.Key_Return,
    Qt.Key.Key_Enter,
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
    Qt.Key.Key_Space,
}
_SHORTCUT_MODIFIERS = (
    Qt.KeyboardModifier.ControlModifier
    | Qt.KeyboardModifier.MetaModifier
    | Qt.KeyboardModifier.AltModifier
)  # (Control is Cmd on a Mac)


#: a pitch wheel sends hundreds of messages a second; the sampler gets at most one a channel per this many ms (the last value always follows)
_BEND_INTERVAL_MS = 15


class _InputFeed(QObject):
    """One open MIDI input. rtmidi calls back on its own thread; the signal hands each message (a list of ints) to the GUI thread."""

    message = Signal(object)

    def __init__(self, name, opener):
        super().__init__()
        self._port = opener(name)
        self._port.set_message_callback(self.message.emit)

    def close(self):
        self._port.set_message_callback(None)
        self._port.close_port()


class _InputCombo(QComboBox):
    """Lists the devices when it is opened, so one plugged in after the window appeared is there."""

    about_to_show = Signal()

    def showPopup(self):
        self.about_to_show.emit()
        super().showPopup()


class AuditionBar(QWidget):
    midi_out = Signal(object)  # one complete channel message (bytes)
    open_changed = Signal(
        bool
    )  # the bar was shown or hidden (the View menu item follows)
    locked_changed = Signal(bool)  # the bar can no longer / can again be opened (the View menu item follows)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sounding = {}  # note -> the channel it was started on (so a note-off follows its note-on even if the channel box changes)
        self._available = False
        self._locked = False
        self._availability = None  # the last (available, why) set_available was given
        self._list_inputs = midi_transport.list_input_names  # (tests swap these two for fakes)
        self._open_input = midi_transport.SharedMidiInput
        self._feed = None
        self._feed_name = ""
        self._input_name = app_config.get_saved_audition_input()  # "" = none
        self._input_sounding = set()  # (channel, note) the input holds: its note-offs must follow even if the bar closes first
        self._keys_held = []
        self._pending_bend = {}  # channel -> the newest pitch bend not sent yet
        self._sampler_bent = set()  # channels whose last pitch bend went out off centre: put back when the input goes
        self._bend_timer = QTimer(self)
        self._bend_timer.setSingleShot(True)
        self._bend_timer.setInterval(_BEND_INTERVAL_MS)
        self._bend_timer.timeout.connect(self._flush_bends)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 4)
        outer.setSpacing(6)

        self.toggle = QToolButton()
        self.toggle.setObjectName("auditionToggle")
        self.toggle.setText("Keyboard")
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setAutoRaise(True)
        self.toggle.setToolTip(HELP)
        self.toggle.clicked.connect(lambda: self.set_open(not self.is_open()))
        self.note = QLabel("")
        self.note.setObjectName("mutedLabel")
        self.note.setWordWrap(True)
        head = QHBoxLayout()
        head.addWidget(
            self.toggle, 0, Qt.AlignmentFlag.AlignLeft
        )  # (left: with the note line hidden the button would otherwise be stretched across the row)
        head.addWidget(self.note, 1)
        outer.addLayout(head)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(6)
        outer.addWidget(self.body)

        row = QHBoxLayout()
        self.channel_combo = QComboBox()
        for number in range(1, 17):
            self.channel_combo.addItem(str(number))
        self.channel_combo.setToolTip("The MIDI channel the notes go out on. Notes from a MIDI input keep the channel they arrive on.")
        self.velocity = QSlider(Qt.Orientation.Horizontal)
        self.velocity.setRange(1, 127)
        self.velocity.setValue(audition.DEFAULT_VELOCITY)
        self.velocity.setMaximumWidth(140)
        self.velocity_label = QLabel()
        self.velocity.valueChanged.connect(
            lambda v: self.velocity_label.setText(str(v))
        )
        self.velocity_label.setText(str(self.velocity.value()))
        self.velocity_label.setMinimumWidth(26)
        self.typing_label = QLabel()
        self.typing_label.setObjectName("mutedLabel")
        self.playing_label = (
            QLabel()
        )  # the notes held right now (mouse or computer keys)
        self.playing_label.setMinimumWidth(150)
        self.playing_label.setToolTip("The notes being played")
        self.input_combo = _InputCombo()
        self.input_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.input_combo.setMinimumContentsLength(14)
        self.input_combo.setMaximumWidth(220)
        self.input_combo.setToolTip(
            "Also play the sampler from a MIDI input: a keyboard, or a virtual port (loopMIDI, IAC) that a DAW plays into. Only notes, pitch bend, mod wheel and sustain are passed on, on the "
            "channel they arrive on. Never the sampler's own port. The device is only held open while this keyboard is open."
        )
        self.input_combo.addItem("None", "")
        if self._input_name:
            self.input_combo.addItem(self._input_name, self._input_name)
            self.input_combo.setCurrentIndex(1)
        self.input_combo.about_to_show.connect(self.refresh_inputs)
        self.input_combo.activated.connect(self._on_input_chosen)
        self.input_status = QLabel()
        self.input_status.setObjectName("mutedLabel")
        self.input_status.setVisible(False)
        self.panic_button = QPushButton("Panic")
        self.panic_button.setToolTip(
            "Silence the channel: hold pedal up, All Sound Off, All Notes Off."
        )
        self.panic_button.clicked.connect(self.panic)
        for widget in (
            QLabel("Channel"),
            self.channel_combo,
            QLabel("Velocity"),
            self.velocity,
            self.velocity_label,
        ):
            row.addWidget(widget)
        row.addSpacing(12)
        row.addWidget(self.typing_label)
        row.addSpacing(12)
        row.addWidget(self.playing_label)
        row.addStretch()
        row.addWidget(QLabel("Input"))
        row.addWidget(self.input_combo)
        row.addWidget(self.input_status)
        row.addSpacing(12)
        row.addWidget(self.panic_button)
        body.addLayout(row)

        self.keyboard = PianoKeyboard()
        self.keyboard.note_on.connect(self._note_on)
        self.keyboard.note_off.connect(self._note_off)
        self.keyboard.typing_base_changed.connect(lambda _n: self._show_typing())
        self.keyboard.held_changed.connect(self._show_playing)
        self.keyboard.velocity_step.connect(self.step_velocity)
        self.keyboard.channel_step.connect(self.step_channel)
        self.keyboard_scroll = ScrollableKeyboard(
            self.keyboard
        )  # (scrolls sideways when the window is too narrow for the keys)
        body.addWidget(self.keyboard_scroll)
        self._show_typing()
        self.set_open(
            False
        )  # always starts closed: an open bar takes over the computer keyboard
        self.set_available(False)

    def _set_note(self, text):
        self.note.setText(text)
        self.note.setVisible(bool(text))  # (an empty line would still take up room)

    # -- open / closed ---------------------------------------------------------------------------------------------------

    def is_open(self):
        return not self.body.isHidden()

    def set_open(self, opened, *, focus=False):
        """Show or hide the keyboard. Hiding it lets go of every note and gives the computer keyboard back; `focus` (the shortcut) also gives the piano the focus.
        A locked bar (see `set_locked`) cannot be opened."""
        if opened and self._locked:
            return
        changed = opened != self.is_open()
        self.body.setVisible(opened)
        self.toggle.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow
        )
        app = QApplication.instance()
        if opened:
            if app is not None:
                app.installEventFilter(self)
            if focus:
                self.keyboard.setFocus()
        else:
            if app is not None:
                app.removeEventFilter(self)
            self.keyboard.release_all()
        self._update_routing()
        self._sync_input()
        if changed:
            self.open_changed.emit(opened)

    def is_locked(self):
        return self._locked

    def set_locked(self, locked, why=""):
        """Lock the bar: closed, and it cannot be opened (the toggle, Cmd+K and the View menu item are off) until it is unlocked. For a long blocking job
        (a sample load over SDS): even a greyed-out bar lagged the load. Not remembered: unlocking leaves it closed. Cheap to call repeatedly."""
        if locked == self._locked:
            return
        self._locked = locked
        if locked:
            self.set_open(False)
        self.toggle.setEnabled(not locked)
        self.toggle.setToolTip(why if locked and why else HELP)
        self.locked_changed.emit(locked)

    # -- owning the computer keyboard ------------------------------------------------------------------------------------

    def routing_keys(self):
        """True while the computer keyboard belongs to the piano: the bar is open and available (a dead keyboard must not swallow typing)."""
        return self.is_open() and self._available

    def _update_routing(self):
        self.keyboard.set_live(self.routing_keys())

    def _window_in_front(self):
        return QApplication.activeWindow() is self.window()

    def eventFilter(self, watched, event):
        kind = event.type()
        if (
            kind not in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease)
            or watched is self.keyboard
            or not self.routing_keys()
        ):
            return False
        if QApplication.activePopupWidget() is not None or not self._window_in_front():
            return False  # a drop-down, a dialog or another window of the app: typing there works as usual
        key = event.key()
        if (
            event.modifiers() & _SHORTCUT_MODIFIERS
            or key in _PASS_THROUGH
            or Qt.Key.Key_F1 <= key <= Qt.Key.Key_F35
        ):
            return False
        if kind == QEvent.Type.KeyPress:
            self.keyboard.keyPressEvent(event)
        else:
            self.keyboard.keyReleaseEvent(event)
        return (
            True  # every other key belongs to the piano, whether or not it plays a note
        )

    def set_available(self, available, why=""):
        """Off while a long job runs: the keys let go of everything first. Cheap to call repeatedly (the window's busy timer does)."""
        if (available, why) == self._availability:
            return
        self._availability = (available, why)
        self._available = available
        if not available:
            self.keyboard.release_all()
        self.body.setEnabled(available)
        self._update_routing()
        self._sync_input()
        self._set_note("" if available else why or self.note.text())

    # -- the keyboard ----------------------------------------------------------------------------------------------------

    def channel(self):
        """0-15."""
        return self.channel_combo.currentIndex()

    def _show_typing(self):
        base = self.keyboard.typing_base
        self.typing_label.setText(
            f"Octave: {midi_note_to_name(base)} to {midi_note_to_name(base + len(TYPING_KEYS) - 1)}"
        )

    def step_velocity(self, direction):
        """Louder (+) or quieter (-): the next multiple of 10, or 1 / 127 at the ends (the C and V keys); the new value is shown in the read-out."""
        self.velocity.setValue(audition.step_velocity(self.velocity.value(), direction))
        self.playing_label.setText(f"Velocity {self.velocity.value()}")

    def step_channel(self, direction):
        """The previous (-) or next (+) MIDI channel (the [ and ] keys); it stops at 1 and 16 rather than wrapping. Notes already sounding keep their channel until released."""
        index = max(0, min(15, self.channel_combo.currentIndex() + direction))
        self.channel_combo.setCurrentIndex(index)
        self.playing_label.setText(f"Channel {index + 1}")

    def _show_playing(self, notes):
        self._keys_held = list(notes)
        self._refresh_playing()

    def _refresh_playing(self):
        notes = sorted(set(self._keys_held) | {note for _channel, note in self._input_sounding})
        self.playing_label.setText("   ".join(midi_note_to_name(n) for n in notes) if notes else "")

    def _note_on(self, note):
        if not self._available:
            return
        self._sounding[note] = self.channel()
        self.midi_out.emit(
            audition.note_on(self._sounding[note], note, self.velocity.value())
        )

    def _note_off(self, note):
        channel = self._sounding.pop(note, None)
        if channel is not None:
            self.midi_out.emit(audition.note_off(channel, note))

    def panic(self):
        self.keyboard.release_all()
        self._release_input_notes()
        for message in audition.panic(self.channel()):
            self.midi_out.emit(message)

    # -- the MIDI input --------------------------------------------------------------------------------------------------

    def input_name(self):
        """The device chosen in the Input box ("" = none), whether or not it is open right now."""
        return self._input_name

    def input_is_open(self):
        return self._feed is not None

    def _sampler_ports(self):
        return {name for name in app_config.get_saved_ports() if name}

    def refresh_inputs(self):
        """Fill the Input box with the devices there are now (not the sampler's own ports), keeping the choice, listed as not connected if it has gone."""
        try:
            names = [name for name in self._list_inputs() if name not in self._sampler_ports()]
        except Exception:
            debug_log.get_logger().error("AuditionBar: couldn't list MIDI inputs", exc_info=True)
            return
        self.input_combo.blockSignals(True)
        self.input_combo.clear()
        self.input_combo.addItem("None", "")
        for name in names:
            self.input_combo.addItem(name, name)
        if self._input_name and self._input_name not in names:
            self.input_combo.addItem(f"{self._input_name} (not connected)", self._input_name)
        self.input_combo.setCurrentIndex(max(0, self.input_combo.findData(self._input_name)))
        self.input_combo.blockSignals(False)

    def _on_input_chosen(self, _index):
        self._input_name = self.input_combo.currentData() or ""
        app_config.save_audition_input(self._input_name)
        debug_log.get_logger().info(f"AuditionBar: MIDI input {self._input_name or 'none'!r}")
        self._set_input_status("")
        self._sync_input()

    def _set_input_status(self, text):
        self.input_status.setText(text)
        self.input_status.setVisible(bool(text))

    def _sync_input(self):
        # open only while the bar is open and nothing is on the sampler's wire: a held device is one a DAW can't have, and a note must not land in a SysEx exchange
        wanted = self._input_name if self.routing_keys() else ""
        if wanted == self._feed_name:
            return
        self._close_input()
        if wanted:
            self._open_feed(wanted)

    def _open_feed(self, name):
        if name in self._sampler_ports():
            self._set_input_status("Sampler's port")
            self.input_status.setToolTip("That is the port the sampler is on. Its own replies would come back as notes.")
            return
        try:
            feed = _InputFeed(name, self._open_input)
        except Exception as e:
            debug_log.get_logger().error(f"AuditionBar: couldn't open MIDI input {name!r}", exc_info=True)
            self._set_input_status("Not found" if "no input port" in str(e) else "Couldn't open")
            self.input_status.setToolTip(str(e))
            return
        feed.message.connect(self._on_input_message)
        self._feed, self._feed_name = feed, name
        self._set_input_status("")
        debug_log.get_logger().info(f"AuditionBar: opened MIDI input {name!r}")

    def _close_input(self):
        feed, self._feed, self._feed_name = self._feed, None, ""
        if feed is not None:
            try:
                feed.close()
            except Exception:
                debug_log.get_logger().error("AuditionBar: couldn't close the MIDI input", exc_info=True)
            feed.deleteLater()
        self._release_input_notes()

    def _release_input_notes(self):
        held, self._input_sounding = self._input_sounding, set()
        for channel, note in sorted(held):
            self.midi_out.emit(audition.note_off(channel, note))
        self._bend_timer.stop()
        for channel in sorted(self._pending_bend):  # (so the last wheel position is the one the sampler has, then it is put back)
            self._emit_bend(self._pending_bend[channel])
        self._pending_bend.clear()
        for channel in sorted(self._sampler_bent):
            self.midi_out.emit(audition.pitch_bend(channel, *audition.PITCH_CENTER))
        self._sampler_bent.clear()
        self.keyboard.set_external_notes(())
        self._refresh_playing()

    def _on_input_message(self, raw):
        if self._feed is None or not self.routing_keys():
            return  # (a message already queued when the input closed)
        message = audition.from_input(raw)
        if message is None:
            return
        kind, channel, data = message[0] & 0xF0, message[0] & 0x0F, message[1]
        if kind == 0xE0:
            self._send_bend(channel, message)
            return
        if kind == 0x90:
            self._input_sounding.add((channel, data))
        elif kind == 0x80:
            self._input_sounding.discard((channel, data))
        elif kind == 0xB0 and data in (120, 123):  # (the sampler lets go of that channel's notes)
            self._input_sounding = {held for held in self._input_sounding if held[0] != channel}
        self.midi_out.emit(message)
        self.keyboard.set_external_notes({note for _channel, note in self._input_sounding})
        self._refresh_playing()

    def _send_bend(self, channel, message):
        # the first bend goes out at once, then at most one per channel every _BEND_INTERVAL_MS, newest value only
        if self._bend_timer.isActive():
            self._pending_bend[channel] = message
            return
        self._emit_bend(message)
        self._bend_timer.start()

    def _flush_bends(self):
        pending, self._pending_bend = self._pending_bend, {}
        for channel in sorted(pending):
            self._emit_bend(pending[channel])
        if pending:
            self._bend_timer.start()

    def _emit_bend(self, message):
        channel = message[0] & 0x0F
        if (message[1], message[2]) == audition.PITCH_CENTER:
            self._sampler_bent.discard(channel)
        else:
            self._sampler_bent.add(channel)
        self.midi_out.emit(message)


def add_audition_action(window, view_menu, bar):
    """The View > Audition Keyboard item (Cmd/Ctrl+K) for `bar`, kept in step with it. Returns the action.

    For a window that sends the notes itself (the S900/S950 editor): it puts the bar in its layout, connects `bar.midi_out`, keeps `bar.set_available(...)` true only while a
    note is safe to send, and calls `bar.set_open(False)` when it closes.
    """
    action = QAction("Audition Keyboard", window)
    action.setCheckable(True)
    action.setShortcut("Ctrl+K")  # (Cmd+K on a Mac)
    action.setToolTip(
        "Show or hide the keyboard along the bottom. Hidden, it plays nothing."
    )
    action.triggered.connect(lambda checked: bar.set_open(checked, focus=checked))
    bar.open_changed.connect(action.setChecked)
    bar.locked_changed.connect(lambda locked: action.setEnabled(not locked))
    view_menu.addAction(action)
    return action
