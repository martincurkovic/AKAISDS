from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
)

from core import sample_editing
from core.audio_preview import SlicePreviewPlayer

_FILTER_TYPE_OPTIONS = [("Lowpass", "lowpass"), ("Highpass", "highpass")]


class FilterSampleDialog(QDialog):
    """Highpass/lowpass filter for the Samples tab's Trim/Reverse/Fade/
    Normalise row (see program_editor_window.py's _confirm_filter_sample).
    Unlike those four (a plain QMessageBox.question confirm - no
    parameters to set), this is the only one of the five with an actual
    PARAMETER (filter type + cutoff frequency), so it gets its own small
    dialog instead - a QFormLayout in the same shape SampleSettingsDialog
    already uses, plus a Preview button.

    Owns its own SlicePreviewPlayer, same pattern SliceEditorWindow
    already established, rather than reusing the Samples tab's own
    preview player - self-contained, doesn't interfere with (or get
    interfered with by) whatever the Samples tab's own click-to-preview
    is doing underneath this modal dialog.

    Preview always plays the WHOLE filtered buffer, never just
    [start, end] - filter_samples itself has no [start, end]-relative
    behaviour at all (same whole-buffer scope as Reverse/Normalise - see
    its own docstring), so there's no marked region to preview against
    here the way the Samples tab's own loop-aware preview has.

    Changing the type or cutoff WHILE a preview is already playing
    re-filters and restarts it automatically at the new settings, rather
    than requiring another Preview click first - this is what lets the
    cutoff be dialed in by ear, the same "hear it change as you adjust"
    spirit as the loop-point live preview already built for the main
    waveform (though this re-filters and restarts from the top each time,
    unlike that one's true live/mid-stream update, since a filter has to
    reprocess the whole buffer - there's no cheap way to swap coefficients
    mid-buffer the way swapping loop bounds is).
    """

    def __init__(self, parent, sample_name, samples, framerate):
        super().__init__(parent)
        self.setWindowTitle(f'Filter Sample - "{sample_name}"')
        self.setModal(True)

        self._samples = samples
        self._framerate = framerate
        self._preview_player = SlicePreviewPlayer(self)

        layout = QFormLayout(self)

        self.type_combo = QComboBox()
        for label, value in _FILTER_TYPE_OPTIONS:
            self.type_combo.addItem(label, value)
        layout.addRow(QLabel("Filter type:"), self.type_combo)

        self.cutoff_spin = QSpinBox()
        # strictly inside (0, Nyquist) - see sample_editing._biquad_
        # coefficients' own clamp for why a cutoff AT or past Nyquist has
        # no valid response at all; this just keeps the spinbox from ever
        # offering a value that clamp would have to silently correct
        nyquist = max(2, int(framerate / 2))
        self.cutoff_spin.setRange(20, nyquist - 1)
        self.cutoff_spin.setSuffix(" Hz")
        self.cutoff_spin.setValue(min(1000, nyquist - 1))
        layout.addRow(QLabel("Cutoff frequency:"), self.cutoff_spin)

        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self._preview)
        layout.addRow(self.preview_button)

        warning_label = QLabel(
            "This overwrites the sample's audio on the sampler and cannot "
            "be undone."
        )
        warning_label.setWordWrap(True)
        layout.addRow(warning_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        self.type_combo.currentIndexChanged.connect(self._maybe_retrigger_preview)
        self.cutoff_spin.valueChanged.connect(self._maybe_retrigger_preview)

    def filter_type(self):
        return self.type_combo.currentData()

    def cutoff_hz(self):
        return self.cutoff_spin.value()

    def _preview(self):
        filtered, *_ = sample_editing.filter_samples(
            self._samples,
            0,
            0,
            len(self._samples) - 1,
            len(self._samples) - 1,
            self.filter_type(),
            self.cutoff_hz(),
            self._framerate,
        )
        self._preview_player.play(filtered, 0, len(filtered) - 1, self._framerate)

    def _maybe_retrigger_preview(self):
        if self._preview_player.is_playing():
            self._preview()

    def accept(self):
        self._preview_player.stop()
        super().accept()

    def reject(self):
        # also what runs on Esc/window-X - QDialog's own default
        # closeEvent() calls reject() when still visible, so overriding
        # this alone (not closeEvent too) covers every way this dialog
        # can close without risking the double-stop/double-emit class of
        # bug a redundant closeEvent override invited elsewhere in this
        # app (see WaveformView.mouseDoubleClickEvent's own fix)
        self._preview_player.stop()
        super().reject()
