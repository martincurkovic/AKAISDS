from PySide6.QtCore import QObject


class PreviewToggle(QObject):
    """The Preview button of the dialogs that process audio (Filter, Time Stretch / Pitch, Reverb): it starts a preview, and while that plays it
    reads "Stop" and stops it. A knob moved meanwhile does NOT restart the preview: it plays out, and the next click starts one with the
    settings as they are then.

    `player` is a `core.audio_preview.SlicePreviewPlayer`; `start` renders the result and plays it. The button follows the player's `finished`
    signal, which fires however playback ends (it ran out, Stop, the dialog closing)."""

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

    def set_available(self, available):
        """Whether a NEW preview can start (the dialog turns it off for a result that is too long); a running one can always be stopped."""
        self._available = available
        self.sync()

    def sync(self, *_args):
        playing = self._player.is_playing()
        self._button.setText("Stop" if playing else "Preview")
        self._button.setEnabled(self._available or playing)
