from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QGroupBox

from ui.knob import Knob


class PreviewToggle(QObject):
    """The Preview button of the dialogs that process audio (Filter, Time Stretch / Pitch, Reverb): it starts a preview, and while that plays it
    reads "Stop" and stops it. A knob moved meanwhile does NOT restart the preview: it plays out, and the next click starts one with the
    settings as they are then.

    `player` is a `core.audio_preview.SlicePreviewPlayer`; `start` renders the result and plays it. The button follows the player's `finished`
    signal, which fires however playback ends (it ran out, Stop, the dialog closing).

    The Space bar does the same as the button, whichever control has the focus. A dialog opens with the focus on its first control (a check box, a
    combo box), and those use Space themselves (toggle, open the list), so the controls are registered with `claim_space_in`, which hands Space to the
    preview before they see it. Push buttons are NOT claimed: on a focused OK or Cancel, Space presses it (as does the Preview button's own, which is
    the same toggle). An open combo's drop-down list is a window of its own and keeps its keys. The dialog's `keyPressEvent` also offers keys to
    `handle_key`, for a Space that lands on the dialog itself."""

    def __init__(self, button, player, start, parent=None):
        super().__init__(parent or button)
        self._button = button
        self._player = player
        self._start = start
        self._available = True
        button.clicked.connect(self.toggle)
        player.finished.connect(self.sync)
        self.sync()

    def toggle(self):
        if self._player.is_playing():
            self._player.stop()
        else:
            self._start()
        self.sync()

    def handle_key(self, event):
        """True if `event` was the Space bar and was used here (a held key repeating is swallowed, not toggled again)."""
        if event.key() != Qt.Key.Key_Space or event.modifiers() != Qt.KeyboardModifier.NoModifier:
            return False
        if not self._button.isEnabled():
            return False
        if not event.isAutoRepeat():
            self.toggle()
        event.accept()
        return True

    #: the kinds of control that take the focus and would use a Space themselves (or, for a knob, pass it up to a checkable group box that would)
    _SPACE_CLAIMED = (Knob, QComboBox, QCheckBox, QGroupBox)

    def claim_space(self, *widgets):
        """Space pressed on any of `widgets` is for the preview, not for the widget."""
        for widget in widgets:
            widget.installEventFilter(self)

    def claim_space_in(self, dialog):
        """`claim_space` for every knob, combo box, check box and group box in `dialog` (see the class docstring)."""
        self.claim_space(*(w for kind in self._SPACE_CLAIMED for w in dialog.findChildren(kind)))

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            if kind == QEvent.Type.KeyPress:
                return self.handle_key(event)
            return self._button.isEnabled()  # (the release of a Space that was used)
        return False

    def set_available(self, available):
        """Whether a NEW preview can start (the dialog turns it off for a result that is too long); a running one can always be stopped."""
        self._available = available
        self.sync()

    def sync(self, *_args):
        playing = self._player.is_playing()
        self._button.setText("Stop" if playing else "Preview")
        self._button.setEnabled(self._available or playing)
