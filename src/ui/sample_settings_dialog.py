from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
)

# Common sample rates offerred in the dropdown
# if a chosen rate isnt an exact integer divisor of the files real rate, sending falls back to original rate with a status message
_RATE_OPTIONS = [
    ("Original", None),
    ("44100 Hz", 44100),
    ("22050 Hz", 22050),
    ("11025 Hz", 11025),
    ("8000 Hz", 8000),
]


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
    ):
        super().__init__(parent)
        self.setWindowTitle(title)

        layout = QFormLayout(self)

        self.name_field = None
        if show_name:
            self.name_field = QLineEdit(name)
            layout.addRow(QLabel("Sample name:"), self.name_field)

        self.bit_depth_spin = QSpinBox()
        self.bit_depth_spin.setRange(8, 16)
        self.bit_depth_spin.setValue(bit_depth)
        self.bit_depth_spin.setToolTip(
            "Bit depths of 14 or lower will transmit faster than 16 bit samples"
        )
        layout.addRow(QLabel("Bit depth:"), self.bit_depth_spin)

        self.rate_combo = QComboBox()
        for label, value in _RATE_OPTIONS:
            self.rate_combo.addItem(label, value)
        idx = self.rate_combo.findData(sample_rate)
        self.rate_combo.setCurrentIndex(idx if idx >= 0 else 0)
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

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_settings(self):
        # returns a dict {"name": str or None, "bit_depth": int,
        # "sample_rate": int or  None, "mono": bool}
        # "name" is None if the dialog was created with show_name-False
        return {
            "name": self.name_field.text().strip() if self.name_field else None,
            "bit_depth": self.bit_depth_spin.value(),
            "sample_rate": self.rate_combo.currentData(),
            "mono": self.mono_checkbox.isChecked() if self.mono_checkbox else False,
        }
