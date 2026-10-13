from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core import preview_pitch, time_stretch
from core.audio_preview import SlicePreviewPlayer
from ui import tooltips
from ui.knob import Knob, LogKnob

#: the same size the Filter dialog's knobs use
_KNOB_SIZE = 56
#: a changed knob re-renders and restarts a playing preview once it has been left alone this long
_RETRIGGER_MS = 350
#: the gap between knob columns
_COLUMN_SPACING = 12

STRETCH = "stretch"
PITCH = "pitch"
_MODES = ((STRETCH, "Time Stretch"), (PITCH, "Pitch Shift"))


def _knob_column(label_text, knob, fmt, tooltip):
    """A label above, the knob, its value below - the Filter dialog's layout. Returns the column as a widget (so a mode can hide it)."""
    column = QVBoxLayout()
    column.setSpacing(4)
    name_label = QLabel(label_text)
    name_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    value_label = QLabel()
    value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(name_label, alignment=Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(knob, alignment=Qt.AlignmentFlag.AlignHCenter)
    column.addWidget(value_label)
    knob.valueChanged.connect(lambda v: value_label.setText(fmt(v)))
    value_label.setText(fmt(knob.value()))
    widget = QWidget()
    widget.setLayout(column)
    widget.setToolTip(tooltip)
    return widget


def _make_knob(knob_class, lo, hi, value, default):
    knob = knob_class()
    knob.setRange(lo, hi)
    knob.setFixedSize(_KNOB_SIZE, _KNOB_SIZE)
    knob.setEnabled(True)  # (a Knob starts read-only)
    knob.setDefaultValue(default)  # what a double-click resets to
    knob.setValue(value)
    return knob


class TimeStretchDialog(QDialog):
    """Time Stretch and Pitch Shift for the Samples tab (core/time_stretch.py), shared by the Program Editor and the Yamaha editor.

    Both are the same granular engine: Time Stretch changes the length and keeps the pitch (Length knob), Pitch Shift changes the
    pitch and keeps the length (Semitones + Cents). Grain size is shared. The knobs are the Filter dialog's (ui/knob.py): Length
    and Grain are LogKnobs (100 % sits in the middle of 25-400 %), a double-click resets one. A Preview plays the result through the
    computer's speakers - nothing is sent anywhere - and a changed knob restarts a preview that is already playing, once it has
    settled. `samples` is the (left) channel to preview, played shifted by `pitch_shift_semitones` (the sample's Tune / Root Note, so it sounds as it will on the sampler); the caller runs the edit from `edit()`."""

    def __init__(self, parent, sample_name, samples, framerate, pitch_shift_semitones=0, allow_new_sample=False, applies_to_working_copy=False, send_name="Send to Sampler"):
        super().__init__(parent)
        self.setWindowTitle(f'Time Stretch / Pitch Shift - "{sample_name}"')
        self.setModal(True)
        self._samples = samples
        self._framerate = framerate
        # Tune / Root Note, as the sampler would play it (see ProgramEditorWindow._preview_pitch_shift_semitones)
        self._pitch_shift_semitones = pitch_shift_semitones
        # "Send as New Sample" (the caller makes a new sample instead of replacing the original) - only where the caller can
        self._allow_new_sample = allow_new_sample
        # the Program Editor: OK edits the sample HERE (its working copy); nothing reaches the sampler yet
        self._applies_to_working_copy = applies_to_working_copy
        self._send_name = send_name
        self._as_new = False
        self._preview_player = SlicePreviewPlayer(self)

        self.mode_combo = QComboBox()
        for key, label in _MODES:
            self.mode_combo.addItem(label, key)
        self.mode_combo.setToolTip(tooltips.TIME_STRETCH_MODE)

        self.length_knob = _make_knob(LogKnob, time_stretch.MIN_PERCENT, time_stretch.MAX_PERCENT, 100, 100)
        self.semitones_knob = _make_knob(Knob, -24, 24, 0, 0)
        self.cents_knob = _make_knob(Knob, -50, 50, 0, 0)
        self.grain_knob = _make_knob(
            LogKnob, time_stretch.MIN_GRAIN_MS, time_stretch.MAX_GRAIN_MS, time_stretch.DEFAULT_GRAIN_MS, time_stretch.DEFAULT_GRAIN_MS
        )

        self._length_column = _knob_column("Length", self.length_knob, lambda v: f"{v} %", tooltips.TIME_STRETCH_LENGTH)
        self._semitones_column = _knob_column("Semitones", self.semitones_knob, lambda v: f"{v:+d}", tooltips.PITCH_SHIFT_SEMITONES)
        self._cents_column = _knob_column("Cents", self.cents_knob, lambda v: f"{v:+d}", tooltips.PITCH_SHIFT_CENTS)
        self._grain_column = _knob_column("Grain", self.grain_knob, lambda v: f"{v} ms", tooltips.TIME_STRETCH_GRAIN)

        self.group = QGroupBox()
        self.group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        # The two modes show different numbers of knobs (Length + Grain, or Semitones + Cents + Grain). Every column gets one width and
        # the row is always wide enough for three, centred, so switching modes never changes what the dialog needs - it used to grow
        # a little each time it was switched to Pitch Shift and back.
        columns = (self._length_column, self._semitones_column, self._cents_column, self._grain_column)
        column_width = max(column.sizeHint().width() for column in columns)
        knob_row = QHBoxLayout(self.group)
        knob_row.setSpacing(_COLUMN_SPACING)
        knob_row.addStretch()
        for column in columns:
            column.setFixedWidth(column_width)
            knob_row.addWidget(column)
        knob_row.addStretch()
        margins = knob_row.contentsMargins()
        self.group.setMinimumWidth(3 * column_width + 2 * _COLUMN_SPACING + margins.left() + margins.right())

        self.result_label = QLabel()
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setToolTip(tooltips.TIME_STRETCH_PREVIEW_BUTTON)
        self.preview_button.clicked.connect(self._preview)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)

        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        self.new_sample_button = None
        if allow_new_sample:
            self.new_sample_button = self._button_box.addButton(
                "Send as New Sample\u2026", QDialogButtonBox.ButtonRole.ActionRole
            )
            self.new_sample_button.setToolTip(tooltips.SEND_AS_NEW_SAMPLE_BUTTON)
            self.new_sample_button.clicked.connect(self._accept_as_new)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        mode_row.addWidget(self.mode_combo, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(mode_row)
        layout.addWidget(self.group)
        layout.addWidget(self.result_label)
        layout.addWidget(self.preview_button)
        layout.addWidget(self.warning_label)
        layout.addWidget(self._button_box)

        self._retrigger = QTimer(self)
        self._retrigger.setSingleShot(True)
        self._retrigger.setInterval(_RETRIGGER_MS)
        self._retrigger.timeout.connect(self._preview)
        self.mode_combo.currentIndexChanged.connect(self._on_changed)
        for knob in (self.length_knob, self.semitones_knob, self.cents_knob, self.grain_knob):
            knob.valueChanged.connect(self._on_changed)
        self._refresh()
        self.setFixedWidth(self.sizeHint().width())
        # the warning wraps to a different number of lines per mode: reserve the taller of the two, so switching modes moves nothing
        label_width = self.width() - layout.contentsMargins().left() - layout.contentsMargins().right()
        tallest = 0
        for mode in (STRETCH, PITCH):
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
            tallest = max(tallest, self.warning_label.heightForWidth(label_width))
        self.mode_combo.setCurrentIndex(0)
        self.warning_label.setMinimumHeight(tallest)

    # -- what the user chose --------------------------------------------------------------------------------

    def mode(self):
        return self.mode_combo.currentData()

    def factor(self):
        """Time Stretch: output length / input length."""
        return self.length_knob.value() / 100.0

    def pitch_ratio(self):
        """Pitch Shift: the playback ratio (2.0 = an octave up)."""
        return time_stretch.pitch_ratio(self.semitones_knob.value(), self.cents_knob.value())

    def grain_ms(self):
        return self.grain_knob.value()

    def grain_frames(self):
        return time_stretch.grain_frames_for(self.grain_ms(), self._framerate)

    def edit(self):
        """(kind, options) for `time_stretch.EDITS` / `time_stretch.transform_for`."""
        if self.mode() == PITCH:
            return PITCH, dict(ratio=self.pitch_ratio(), grain_frames=self.grain_frames())
        return STRETCH, dict(factor=self.factor(), grain_frames=self.grain_frames())

    def summary(self):
        """The chosen edit in words, for a confirmation: 'stretched to 150% of its length' / 'shifted by +3.1 semitones'."""
        if self.mode() == PITCH:
            total = self.semitones_knob.value() + self.cents_knob.value() / 100
            return f"shifted by {total:+g} semitones"
        return f"stretched to {self.length_knob.value()}% of its length"

    # -- state ---------------------------------------------------------------------------------------------

    def _out_frames(self):
        if self.mode() == PITCH:
            return len(self._samples)
        return time_stretch.output_length(len(self._samples), self.factor())

    def _changes_anything(self):
        if self.mode() == PITCH:
            return self.semitones_knob.value() != 0 or self.cents_knob.value() != 0
        return self.length_knob.value() != 100

    def _refresh(self):
        pitch = self.mode() == PITCH
        self._length_column.setVisible(not pitch)
        self._semitones_column.setVisible(pitch)
        self._cents_column.setVisible(pitch)
        kept = "The length is kept." if pitch else "The pitch is kept."
        if self._applies_to_working_copy:
            text = f"{kept} OK applies it to the sample here; nothing is sent until you click {self._send_name}."
            if self._allow_new_sample:
                text += " Send as New Sample sends it now and keeps the original."
            self.warning_label.setText(text)
        elif self._allow_new_sample:
            self.warning_label.setText(f"{kept} OK overwrites the sample and cannot be undone; Send as New Sample keeps the original.")
        else:
            self.warning_label.setText(f"{kept} This changes the sample and cannot be undone.")
        frames = self._out_frames()
        too_long = frames > time_stretch.MAX_OUTPUT_FRAMES
        self.result_label.setText(f"Too long ({frames / self._framerate:,.0f} s)" if too_long else self._result_text(frames))
        # nothing to do at 100% / 0 semitones and 0 cents
        can_send = self._changes_anything() and not too_long
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(can_send)
        if self.new_sample_button is not None:
            self.new_sample_button.setEnabled(can_send)
        self.preview_button.setEnabled(not too_long)

    def _result_text(self, frames):
        """'N frames, x.xx s' - and, when the sample's Tune / Root Note make the preview play faster or slower than its recorded rate,
        how long that takes as you will hear it: '(y.yy s as previewed)'."""
        text = f"{frames:,} frames, {frames / self._framerate:.2f} s"
        if abs(self._pitch_shift_semitones) >= 0.005:
            heard = preview_pitch.played_seconds(frames, self._framerate, self._pitch_shift_semitones)
            text += f" ({heard:.2f} s as previewed)"
        return text

    def _on_changed(self, *_args):
        self._refresh()
        if self._preview_player.is_playing():
            self._retrigger.start()

    def _preview(self):
        if self._out_frames() > time_stretch.MAX_OUTPUT_FRAMES:
            return
        kind, options = self.edit()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = time_stretch.transform_for(kind, **options)(self._samples, 0, 0, len(self._samples) - 1, len(self._samples) - 1)[0]
        finally:
            QApplication.restoreOverrideCursor()
        self._preview_player.play(
            result, 0, len(result) - 1, self._framerate, pitch_shift_semitones=self._pitch_shift_semitones
        )

    def send_as_new(self):
        """True if the user chose "Send as New Sample" rather than OK."""
        return self._as_new

    def _accept_as_new(self):
        self._as_new = True
        self.accept()

    def accept(self):
        self._retrigger.stop()
        self._preview_player.stop()
        super().accept()

    def reject(self):
        # also Esc / the window's X (QDialog's closeEvent calls reject)
        self._retrigger.stop()
        self._preview_player.stop()
        super().reject()
