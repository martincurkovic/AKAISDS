from PySide6.QtCore import Qt
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
)

from core import preview_pitch, reverb
from core.audio_preview import SlicePreviewPlayer
from ui import tooltips
from ui.knob import Knob
from ui.preview_toggle import PreviewToggle
from ui.time_stretch_dialog import _COLUMN_SPACING, _knob_column, _make_knob

_CUSTOM = "Custom"


def _decay_text(value):
    seconds = reverb.rt60_seconds(value)
    return f"{seconds:.1f} s" if seconds < 10 else f"{seconds:.0f} s"


def _size_text(value):
    return f"x{reverb.size_scale(value):.1f}"


def _damping_text(value):
    step = reverb.damping_step(value)
    return "none" if step == 0 else f"{step} / 5"


def _mix_text(value):
    return f"{round(value * 100 / 99)} %"


class ReverbDialog(QDialog):
    """Reverb for the Samples tab (core/reverb.py): four knobs and a Preview that plays the result through the computer's speakers.

    Same shape as the Time Stretch dialog (and its knobs, which are the Filter dialog's): OK applies it to the sample HERE (the Program Editor's
    working copy) and nothing reaches the sampler yet; "Send as New Sample" sends the result now and keeps the original. Preview becomes Stop
    while it plays; a knob moved meanwhile does not restart it (ui/preview_toggle.py). `samples` is the channel to preview, played shifted by `pitch_shift_semitones` (the
    sample's Tune / Root Note, so it sounds as it will on the sampler); the caller runs the edit from `options()`."""

    def __init__(self, parent, sample_name, samples, framerate, pitch_shift_semitones=0, allow_new_sample=False, applies_to_working_copy=False, send_name="Send to Sampler"):
        super().__init__(parent)
        self.setWindowTitle(f'Reverb - "{sample_name}"')
        self.setModal(True)
        self._samples = samples
        self._framerate = framerate
        self._pitch_shift_semitones = pitch_shift_semitones
        self._allow_new_sample = allow_new_sample
        self._applies_to_working_copy = applies_to_working_copy
        self._send_name = send_name
        self._as_new = False
        self._preview_player = SlicePreviewPlayer(self)

        decay, size, damping, mix = reverb.DEFAULTS
        self.decay_knob = _make_knob(Knob, 0, 99, decay, decay)
        self.size_knob = _make_knob(Knob, 0, 99, size, size)
        self.damping_knob = _make_knob(Knob, 0, 99, damping, damping)
        self.mix_knob = _make_knob(Knob, 0, 99, mix, mix)
        columns = (
            _knob_column("Decay", self.decay_knob, _decay_text, tooltips.REVERB_DECAY),
            _knob_column("Size", self.size_knob, _size_text, tooltips.REVERB_SIZE),
            _knob_column("Damping", self.damping_knob, _damping_text, tooltips.REVERB_DAMPING),
            _knob_column("Mix", self.mix_knob, _mix_text, tooltips.REVERB_MIX),
        )

        self.preset_combo = QComboBox()
        self.preset_combo.addItem(_CUSTOM, None)
        for name, values in reverb.PRESETS.items():
            self.preset_combo.addItem(name, values)
        self.preset_combo.setToolTip(tooltips.REVERB_PRESET)

        self.group = QGroupBox()
        self.group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        knob_row = QHBoxLayout(self.group)
        knob_row.setSpacing(_COLUMN_SPACING)
        column_width = max(column.sizeHint().width() for column in columns)
        knob_row.addStretch()
        for column in columns:
            column.setFixedWidth(column_width)
            knob_row.addWidget(column)
        knob_row.addStretch()

        self.result_label = QLabel()
        self.result_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.preview_button = QPushButton("Preview")
        self.preview_button.setToolTip(tooltips.TIME_STRETCH_PREVIEW_BUTTON)
        self._preview_toggle = PreviewToggle(self.preview_button, self._preview_player, self._preview, self)
        self.warning_label = QLabel()
        self.warning_label.setWordWrap(True)

        self._button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        self.new_sample_button = None
        if allow_new_sample:
            self.new_sample_button = self._button_box.addButton("Send as New Sample…", QDialogButtonBox.ButtonRole.ActionRole)
            self.new_sample_button.setToolTip(tooltips.SEND_AS_NEW_SAMPLE_BUTTON)
            self.new_sample_button.clicked.connect(self._accept_as_new)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        preset_row.addWidget(self.preset_combo, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(preset_row)
        layout.addWidget(self.group)
        layout.addWidget(self.result_label)
        layout.addWidget(self.preview_button)
        layout.addWidget(self.warning_label)
        layout.addWidget(self._button_box)

        self.preset_combo.activated.connect(self._on_preset_chosen)
        for knob in self._knobs():
            knob.valueChanged.connect(self._on_changed)
        self._preview_toggle.claim_space_in(self)
        self._refresh()
        self.setFixedWidth(self.sizeHint().width())

    def _knobs(self):
        return (self.decay_knob, self.size_knob, self.damping_knob, self.mix_knob)

    # -- what the user chose --------------------------------------------------------------------------------

    def options(self):
        """The keyword arguments for `core.reverb.transform_for`."""
        return dict(
            decay=self.decay_knob.value(),
            size=self.size_knob.value(),
            damping=self.damping_knob.value(),
            mix=self.mix_knob.value(),
            framerate=self._framerate,
        )

    def summary(self):
        """The chosen reverb in words, for the log: 'decay 0.7 s, size x1.0, damping 1 / 5, mix 30 %'."""
        return (
            f"decay {_decay_text(self.decay_knob.value())}, size {_size_text(self.size_knob.value())}, "
            f"damping {_damping_text(self.damping_knob.value())}, mix {_mix_text(self.mix_knob.value())}"
        )

    # -- state ---------------------------------------------------------------------------------------------

    def _longest_result(self):
        return reverb.max_output_frames(len(self._samples), self.decay_knob.value(), self._framerate)

    def _refresh(self):
        if self._applies_to_working_copy:
            text = f"OK applies it to the sample here; nothing is sent until you click {self._send_name}. The sample gets longer by the tail."
            if self._allow_new_sample:
                text += " Send as New Sample sends it now and keeps the original."
        elif self._allow_new_sample:
            text = "OK overwrites the sample and cannot be undone; Send as New Sample keeps the original. The sample gets longer by the tail."
        else:
            text = "This changes the sample and cannot be undone. The sample gets longer by the tail."
        self.warning_label.setText(text)
        longest = self._longest_result()
        too_long = longest > reverb.MAX_OUTPUT_FRAMES
        self.result_label.setText(f"Too long ({longest / self._framerate:,.0f} s)" if too_long else self._result_text(longest))
        can_send = reverb.changes_anything(self.mix_knob.value()) and not too_long
        self._button_box.button(QDialogButtonBox.StandardButton.Ok).setEnabled(can_send)
        if self.new_sample_button is not None:
            self.new_sample_button.setEnabled(can_send)
        self._preview_toggle.set_available(not too_long)

    def _result_text(self, longest):
        """What the length will be at most: the tail stops early once it has died away, so the result is often shorter."""
        added = longest - len(self._samples)
        text = f"{len(self._samples) / self._framerate:.2f} s, plus up to {added / self._framerate:.1f} s of tail"
        if abs(self._pitch_shift_semitones) >= 0.005:
            heard = preview_pitch.played_seconds(longest, self._framerate, self._pitch_shift_semitones)
            text += f" ({heard:.2f} s as previewed)"
        return text

    def _on_preset_chosen(self, index):
        values = self.preset_combo.itemData(index)
        if values is None:
            return
        for knob, value in zip(self._knobs(), values):
            knob.setValue(value)  # (each triggers _on_changed, which keeps the preset because it matches)

    def _matching_preset_index(self):
        values = tuple(knob.value() for knob in self._knobs())
        for index in range(1, self.preset_combo.count()):
            if tuple(self.preset_combo.itemData(index)) == values:
                return index
        return 0

    def _on_changed(self, *_args):
        self.preset_combo.setCurrentIndex(self._matching_preset_index())
        self._refresh()  # (a preview that is playing plays out; see PreviewToggle)

    def _preview(self):
        if self._longest_result() > reverb.MAX_OUTPUT_FRAMES or not reverb.changes_anything(self.mix_knob.value()):
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            result = reverb.transform_for(**self.options())(self._samples, 0, 0, len(self._samples) - 1, len(self._samples) - 1)[0]
        finally:
            QApplication.restoreOverrideCursor()
        self._preview_player.play(result, 0, len(result) - 1, self._framerate, pitch_shift_semitones=self._pitch_shift_semitones)

    def send_as_new(self):
        """True if the user chose "Send as New Sample" rather than OK."""
        return self._as_new

    def _accept_as_new(self):
        self._as_new = True
        self.accept()

    def keyPressEvent(self, event):
        if not self._preview_toggle.handle_key(event):
            super().keyPressEvent(event)

    def accept(self):
        self._preview_player.stop()
        super().accept()

    def reject(self):
        # also Esc / the window's X (QDialog's closeEvent calls reject)
        self._preview_player.stop()
        super().reject()
