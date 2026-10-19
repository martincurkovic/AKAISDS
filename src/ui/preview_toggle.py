from PySide6.QtCore import QEvent, QObject, Qt


class PreviewToggle(QObject):
    """The Preview button of the dialogs that process audio (Filter, Time Stretch / Pitch, Reverb): it starts a preview, and while that plays it
    reads "Stop" and stops it. A knob moved meanwhile does NOT restart the preview: it plays out, and the next click starts one with the
    settings as they are then.

    `player` is a `core.audio_preview.SlicePreviewPlayer`; `start` renders the result and plays it. The button follows the player's `finished`
    signal, which fires however playback ends (it ran out, Stop, the dialog closing).

    The Space bar does the same as the button: the dialog's `keyPressEvent` offers each key to `handle_key` first. A key only gets that far when the
    focused widget did not use it, so Space on a focused OK / Cancel button, a combo or a check box still does what it always did. A knob does not
    use Space either, but the check box of a group box around it (the Filter dialog's) would take it on the way up and flip the filter's bypass,
    so the knobs are registered with `claim_space`, which hands Space to the preview before it travels any further."""

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

    def claim_space(self, *widgets):
        """Space pressed on any of `widgets` (the dialog's knobs) is for the preview, not for whatever is around them."""
        for widget in widgets:
            widget.installEventFilter(self)

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
