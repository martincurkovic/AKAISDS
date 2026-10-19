"""The keyboard bar at the bottom of the Program Editor: a piano to hear the sampler. Same behaviour as JV-Studio's audition bar, minus its patch browser and Master slider.

Nothing here writes to the sampler. It only emits what to send (`midi_out`, one complete channel message at a time); the window decides whether it is safe to send.

While the bar is OPEN (and available) it owns the computer keyboard, as Ableton's computer MIDI keyboard does: every key press that is not a shortcut (Ctrl/Cmd/Alt) or a
navigation key (Tab, arrows, Esc, Enter, F-keys) is routed to the piano, whatever has the focus, so nothing can be typed into a text box. It stands aside while another window is
in front (a dialog, the Slice Editor) or a drop-down is open. Cmd+K closes the bar and gives the keys back. The bar always starts closed.
"""

from PySide6.QtCore import QEvent, Qt, Signal
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

from core import audition
from core.midi_notes import midi_note_to_name
from ui.keyboard import TYPING_KEYS, PianoKeyboard, ScrollableKeyboard

HELP = (
    "Play the sampler from the keyboard below.\n"
    "While open, the computer keyboard keys A-L play notes, Z and X change the octave, C and V the velocity. "
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


class AuditionBar(QWidget):
    midi_out = Signal(object)  # one complete channel message (bytes)
    open_changed = Signal(
        bool
    )  # the bar was shown or hidden (the View menu item follows)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sounding = {}  # note -> the channel it was started on (so a note-off follows its note-on even if the channel box changes)
        self._available = False
        self._availability = None  # the last (available, why) set_available was given
        self._demo = False
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
        self.channel_combo.setToolTip("The MIDI channel the notes go out on.")
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
        row.addWidget(self.panic_button)
        body.addLayout(row)

        self.keyboard = PianoKeyboard()
        self.keyboard.note_on.connect(self._note_on)
        self.keyboard.note_off.connect(self._note_off)
        self.keyboard.typing_base_changed.connect(lambda _n: self._show_typing())
        self.keyboard.held_changed.connect(self._show_playing)
        self.keyboard.velocity_step.connect(self.step_velocity)
        self.keyboard_scroll = ScrollableKeyboard(
            self.keyboard
        )  # (scrolls sideways when the window is too narrow for the keys)
        body.addWidget(self.keyboard_scroll)
        self._show_typing()
        self.set_open(
            False
        )  # always starts closed: an open bar takes over the computer keyboard
        self.set_available(False)

    def _idle_note(self):
        return "Demo: the simulated sampler makes no sound." if self._demo else ""

    def _set_note(self, text):
        self.note.setText(text)
        self.note.setVisible(bool(text))  # (an empty line would still take up room)

    def set_demo(self, demo):
        self._demo = demo
        if self._available:
            self._set_note(self._idle_note())

    # -- open / closed ---------------------------------------------------------------------------------------------------

    def is_open(self):
        return not self.body.isHidden()

    def set_open(self, opened, *, focus=False):
        """Show or hide the keyboard. Hiding it lets go of every note and gives the computer keyboard back; `focus` (the shortcut) also gives the piano the focus."""
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
        if changed:
            self.open_changed.emit(opened)

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
        self._set_note(self._idle_note() if available else why or self.note.text())

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

    def _show_playing(self, notes):
        self.playing_label.setText(
            "   ".join(midi_note_to_name(n) for n in notes) if notes else ""
        )

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
        for message in audition.panic(self.channel()):
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
    view_menu.addAction(action)
    return action
