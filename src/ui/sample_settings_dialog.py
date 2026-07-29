from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QLayout,
    QSpinBox,
)
from ui.qt_helpers import widen_popup_to_fit_items

# Common sample rates offerred in the dropdown
# if a chosen rate isnt an exact integer divisor of the files real rate, sending falls back to original rate with a status message
_RATE_OPTIONS = [
    ("Original", None),
    ("44100 Hz", 44100),
    ("30000 Hz", 30000),
    ("22050 Hz", 22050),
    ("15000 Hz", 15000),
    ("11025 Hz", 11025),
    ("8000 Hz", 8000),
]

_BIT_DEPTH_OPTIONS = [8, 10, 12, 14, 16]


class SampleSettingsDialog(QDialog):
    # shared dialog for adjusting name/bitdepth/samplerate/mono
    # used for both individual samples and global settings
    # pass show_name=False for global dialog

    def __init__(
        self,
        parent=None,
        title="Sample Settings",
        show_name=True,
        name="",
        bit_depth=16,
        sample_rate=None,
        mono=False,
        show_mono=True,
        show_starting_slot=False,
        starting_sample_number=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(title)

        layout = QFormLayout(self)

        self.name_field = None
        if show_name:
            self.name_field = QLineEdit(name)
            layout.addRow(QLabel("Sample name:"), self.name_field)

        self.bit_depth_combo = QComboBox()
        for depth in _BIT_DEPTH_OPTIONS:
            self.bit_depth_combo.addItem(f"{depth}-bit", depth)
        closest = min(_BIT_DEPTH_OPTIONS, key=lambda d: abs(d - bit_depth))
        self.bit_depth_combo.setCurrentIndex(_BIT_DEPTH_OPTIONS.index(closest))
        self.bit_depth_combo.setToolTip(
            "Bit depths of 14 or lower will transmit faster than 16 bit samples"
        )
        self.bit_depth_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents
        )
        widen_popup_to_fit_items(self.bit_depth_combo)
        layout.addRow(QLabel("Bit depth:"), self.bit_depth_combo)

        self.rate_combo = QComboBox()
        for label, value in _RATE_OPTIONS:
            self.rate_combo.addItem(label, value)
        idx = self.rate_combo.findData(sample_rate)
        self.rate_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.rate_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        widen_popup_to_fit_items(self.rate_combo)
        layout.addRow(QLabel("Sample rate:"), self.rate_combo)

        self.mono_checkbox = None
        if show_mono:
            self.mono_checkbox = QCheckBox("Send as mono (left channel only)")
            self.mono_checkbox.setChecked(mono)
            self.mono_checkbox.setToolTip(
                "Only applies to stereo files - sends just the left\n"
                "channel as a single sample instead of a -L/-R pair."
            )
            layout.addRow(self.mono_checkbox)

        self.starting_slot_spin = None
        if show_starting_slot:
            self.starting_slot_spin = QSpinBox()
            self.starting_slot_spin.setRange(-1, 127)
            self.starting_slot_spin.setSpecialValueText("(not set)")
            self.starting_slot_spin.setValue(
                -1 if starting_sample_number is None else starting_sample_number
            )
            self.starting_slot_spin.setToolTip(
                "Required for a Generic SDS device - it has no way to\n"
                "auto-detect which slots are already in use, unlike an\n"
                "Akai. Ignored entirely when talking to an Akai sampler,\n"
                "which figures this out on its own."
            )
            layout.addRow(QLabel("Starting sample number:"), self.starting_slot_spin)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        # fixed window size - computed from actual content rather than hardcoded value
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def get_settings(self):
        # returns a dict {"name": str or None, "bit_depth": int,
        # "sample_rate": int or  None, "mono": bool}
        # "name" is None if the dialog was created with show_name-False
        # starting_sample_number is None if dialog was created with show_starting_slot=False
        # or if it was was left at (not set)
        starting_sample_number = None
        if self.starting_slot_spin is not None:
            value = self.starting_slot_spin.value()
            starting_sample_number = None if value == -1 else value
        return {
            "name": self.name_field.text().strip() if self.name_field else None,
            "bit_depth": self.bit_depth_combo.currentData(),
            "sample_rate": self.rate_combo.currentData(),
            "mono": self.mono_checkbox.isChecked() if self.mono_checkbox else False,
            "starting_sample_number": starting_sample_number,
        }
