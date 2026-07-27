from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QLayout,
)

_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _midi_note_to_name(note_number):
    # midi note number -> name under convention such that note 60 = middle C (aka C4)
    name = _NOTE_NAMES[note_number % 12]
    octave = (note_number // 12) - 1
    return f"{name}{octave}"


def _format_size(sample_length):
    # akai always store sample audio data as 16 bit (2 bytes per sample word)
    # regardless of how it was originally sent - so size in bytes is just sample_length * 2
    size_bytes = sample_length * 2
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    return f"{size_bytes / 1024:.1f} KB"


class SampleInfoDialog(QDialog):
    # shows hardware sample's specs (read only other than name field)
    # rename command only happens when user clicks ok

    def __init__(self, parent, info):
        super().__init__(parent)
        self.setWindowTitle(f"Sample Info = {info['name']}")

        layout = QFormLayout(self)

        self.name_field = QLineEdit(info["name"])
        layout.addRow(QLabel("Name:"), self.name_field)

        layout.addRow(QLabel("Sample number:"), QLabel(str(info["sample_number"])))
        layout.addRow(QLabel("Bit depth:"), QLabel(f"{info['bit_depth']}-bit"))
        layout.addRow(QLabel("Sample rate:"), QLabel(f"{info['sample_rate']} Hz"))

        sample_rate = info["sample_rate"] or 1  # guard against a zero rate
        duration_s = info["sample_length"] / sample_rate
        size_str = _format_size(info["sample_length"])
        layout.addRow(
            QLabel("Length:"),
            QLabel(f"{duration_s:.3f} s - ({size_str})"),
        )

        note_name = _midi_note_to_name(info["root_key"])
        layout.addRow(
            QLabel("Root key:"),
            QLabel(f"{note_name} (MIDI note {info['root_key']})"),
        )

        detune = info["detune"]
        sign = "+" if detune >= 0 else ""
        layout.addRow(QLabel("Detune:"), QLabel(f"{sign}{detune:.2f} semitones"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        # fixed window size - computed form actual content rather than hardcoded value
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def get_new_name(self):
        return self.name_field.text().strip()
