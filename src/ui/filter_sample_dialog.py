from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)
from PySide6.QtCore import Qt

from core import sample_editing
from core.audio_preview import SlicePreviewPlayer
from ui.knob import LogKnob
from ui import tooltips

# dB/octave -> cascaded biquad stage count - each stage is a fixed 12dB/
# octave (see sample_editing._biquad_coefficients/_stages_for_slope), so
# this is just "how many times to run the filter over its own output"
_SLOPE_OPTIONS = [(f"{db} dB/octave", db) for db in (12, 24, 36, 48)]

_KNOB_SIZE = 56  # same size program_editor_window.py's own primary knobs use


def _build_knob_column(label_text, knob):
    # same label-above/value-below shape as program_editor_window.py's own
    # _build_knob_column - duplicated rather than imported to avoid this
    # dialog depending on ProgramEditorWindow (a plain QWidget method, not
    # something meant to be called from outside that class)
    column = QVBoxLayout()
    column.setSpacing(4)

    name_label = QLabel(label_text)
    name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)

    value_label = QLabel()
    value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

    column.addWidget(knob, alignment=Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(value_label)

    knob.valueChanged.connect(lambda v: value_label.setText(f"{v} Hz"))
    value_label.setText(f"{knob.value()} Hz")

    return column


def _build_slope_combo():
    combo = QComboBox()
    for label, value in _SLOPE_OPTIONS:
        combo.addItem(label, value)
    return combo


class FilterSampleDialog(QDialog):
    """Highpass + lowpass filter for the Samples tab's Trim/Reverse/Fade/
    Normalise row (see program_editor_window.py's _confirm_filter_sample).
    Unlike those four (a plain QMessageBox.question confirm - no
    parameters), this needs real controls, so it gets its own dialog.

    Both filters can run at once, in series (highpass first, then lowpass
    - see sample_editing.filter_samples' own docstring on why that order),
    each independently bypassable via its own QGroupBox's checkbox - a
    checkable QGroupBox already greys out its own contents when
    unchecked, which is exactly "bypass this filter" for free, no manual
    enable/disable wiring needed - also automatically greys the knob's own
    paint (Knob/LogKnob check isEnabled() directly, since they paint
    themselves with QPainter rather than through the stylesheet). Cutoff
    is a LogKnob (ui/knob.py - the same rotary control the Program/
    Keygroup tabs use throughout this app, but logarithmic: equal
    rotation covers equal RATIO, matching how a real EQ's own frequency
    knob works, so 20Hz-200Hz gets the same rotational space as
    2kHz-20kHz) per filter. Double-clicking a knob resets it to "fully
    open" - its own minimum for highpass, maximum for lowpass - not some
    shared default, since "fully open" means something different for
    each (see their own construction comments below). Slope (12/24/36/48
    dB/octave - how many identical biquad stages are cascaded, see
    sample_editing._stages_for_slope) is its own dropdown per filter, not
    shared - a gentle highpass alongside a steep lowpass (or vice versa)
    is a real, useful combination.

    Preview always plays the WHOLE buffer run through whichever filters
    are currently enabled, at their current settings - never just
    [start, end] (filter_samples has no [start, end]-relative behaviour
    at all, see its own docstring). Changing any control WHILE a preview
    is already playing re-filters and restarts it automatically, so the
    cutoff/slope/bypass combination can be dialled in by ear without
    repeated Preview clicks - same spirit as the loop-point live preview
    already built for the main waveform, though (like the original
    single-filter version of this dialog) this always reprocesses the
    whole buffer from the top rather than truly live mid-stream, since a
    filter has no cheap way to swap coefficients mid-buffer the way
    swapping loop bounds is.

    Owns its own SlicePreviewPlayer (same pattern SliceEditorWindow
    already established) rather than reusing the Samples tab's own
    preview player - self-contained, doesn't interfere with (or get
    interfered with by) whatever the Samples tab's own click-to-preview is
    doing underneath this modal dialog.
    """

    def __init__(self, parent, sample_name, samples, framerate):
        super().__init__(parent)
        self.setWindowTitle(f'Filter Sample - "{sample_name}"')
        self.setModal(True)

        self._samples = samples
        self._framerate = framerate
        self._preview_player = SlicePreviewPlayer(self)

        # strictly inside (0, Nyquist) - see sample_editing._biquad_
        # coefficients' own clamp for why a cutoff AT or past Nyquist has
        # no valid response at all; this just keeps the knobs from ever
        # offering a value that clamp would have to silently correct
        nyquist = max(2, int(framerate / 2))
        knob_max = nyquist - 1

        self.hp_group = QGroupBox("Highpass")
        self.hp_group.setCheckable(True)
        self.hp_group.setChecked(False)
        self.hp_knob = LogKnob()
        self.hp_knob.setRange(20, knob_max)
        self.hp_knob.setFixedSize(_KNOB_SIZE, _KNOB_SIZE)
        self.hp_knob.setEnabled(True)
        # double-click resets to "fully open" - for a highpass that's its
        # own MINIMUM (cutoff at the very bottom = lets everything
        # through, i.e. no real filtering) - deliberately NOT the same as
        # the knob's own initial displayed value below (setValue), which
        # is a sensible non-extreme starting point instead. defaultValue
        # only controls the double-click target, never the opening value.
        self.hp_knob.setDefaultValue(20)
        self.hp_knob.setValue(min(200, knob_max))
        self.hp_slope_combo = _build_slope_combo()
        hp_layout = QVBoxLayout(self.hp_group)
        hp_layout.addLayout(_build_knob_column("Cutoff", self.hp_knob))
        hp_layout.addWidget(QLabel("Slope:"))
        hp_layout.addWidget(self.hp_slope_combo)

        self.lp_group = QGroupBox("Lowpass")
        self.lp_group.setCheckable(True)
        self.lp_group.setChecked(True)  # a sensible non-empty starting state
        self.lp_knob = LogKnob()
        self.lp_knob.setRange(20, knob_max)
        self.lp_knob.setFixedSize(_KNOB_SIZE, _KNOB_SIZE)
        self.lp_knob.setEnabled(True)
        # "fully open" for a lowpass is its own MAXIMUM (cutoff at the
        # very top = lets everything through) - same reasoning as the
        # highpass knob above, mirrored
        self.lp_knob.setDefaultValue(knob_max)
        self.lp_knob.setValue(min(1000, knob_max))
        self.lp_slope_combo = _build_slope_combo()
        lp_layout = QVBoxLayout(self.lp_group)
        lp_layout.addLayout(_build_knob_column("Cutoff", self.lp_knob))
        lp_layout.addWidget(QLabel("Slope:"))
        lp_layout.addWidget(self.lp_slope_combo)

        for group in (self.hp_group, self.lp_group):
            group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        sections_row = QHBoxLayout()
        sections_row.addWidget(self.hp_group)
        sections_row.addWidget(self.lp_group)

        self.preview_button = QPushButton("Preview")
        self.preview_button.setToolTip(tooltips.FILTER_PREVIEW_BUTTON)
        self.preview_button.clicked.connect(self._preview)

        warning_label = QLabel(
            "This overwrites the sample's audio on the sampler and cannot "
            "be undone."
        )
        warning_label.setWordWrap(True)

        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(sections_row)
        layout.addWidget(self.preview_button)
        layout.addWidget(warning_label)
        layout.addWidget(self._button_box)

        for signal in (
            self.hp_group.toggled,
            self.hp_knob.valueChanged,
            self.hp_slope_combo.currentIndexChanged,
            self.lp_group.toggled,
            self.lp_knob.valueChanged,
            self.lp_slope_combo.currentIndexChanged,
        ):
            signal.connect(self._maybe_retrigger_preview)
        self.hp_group.toggled.connect(self._update_ok_enabled)
        self.lp_group.toggled.connect(self._update_ok_enabled)
        self._update_ok_enabled()

    def _update_ok_enabled(self):
        # nothing to send if BOTH filters are bypassed - matches
        # normalize/etc's own "nothing to do" guards, just enforced by
        # disabling OK outright rather than a popup once clicked, since
        # there's already a visible reason (both group boxes unchecked)
        ok_button = self._button_box.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setEnabled(self.hp_group.isChecked() or self.lp_group.isChecked())

    def highpass_enabled(self):
        return self.hp_group.isChecked()

    def highpass_cutoff_hz(self):
        return self.hp_knob.value()

    def highpass_slope_db_per_octave(self):
        return self.hp_slope_combo.currentData()

    def lowpass_enabled(self):
        return self.lp_group.isChecked()

    def lowpass_cutoff_hz(self):
        return self.lp_knob.value()

    def lowpass_slope_db_per_octave(self):
        return self.lp_slope_combo.currentData()

    def _preview(self):
        filtered, *_ = sample_editing.filter_samples(
            self._samples,
            0,
            0,
            len(self._samples) - 1,
            len(self._samples) - 1,
            self._framerate,
            highpass_enabled=self.highpass_enabled(),
            highpass_cutoff_hz=self.highpass_cutoff_hz(),
            highpass_slope_db_per_octave=self.highpass_slope_db_per_octave(),
            lowpass_enabled=self.lowpass_enabled(),
            lowpass_cutoff_hz=self.lowpass_cutoff_hz(),
            lowpass_slope_db_per_octave=self.lowpass_slope_db_per_octave(),
        )
        self._preview_player.play(filtered, 0, len(filtered) - 1, self._framerate)

    def _maybe_retrigger_preview(self, *_args):
        if self._preview_player.is_playing() and (
            self.highpass_enabled() or self.lowpass_enabled()
        ):
            self._preview()
        elif self._preview_player.is_playing():
            # both just got bypassed mid-preview - nothing left to play
            self._preview_player.stop()

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
