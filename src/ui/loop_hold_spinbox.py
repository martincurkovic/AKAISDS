from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QSpinBox


class LoopHoldSpinBox(QSpinBox):
    """A QSpinBox for a sample's loop hold/dwell time (LDWELL1). Confirmed
    on a real S2000, matching s3k.params' own LDWELL1 notes: 0 is "Off" (no
    loop), 9999 is "Hold" (loop forever - the common case and this widget's
    own default), and 1-9998 is a plain dwell time in milliseconds.

    Double-clicking the field jumps straight to Hold regardless of the
    current value - the most common state, but an awkward one to reach by
    scrolling/typing since it sits at the very top of the range.
    """

    OFF_VALUE = 0
    HOLD_VALUE = 9999

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(self.OFF_VALUE, self.HOLD_VALUE)
        self.setValue(self.HOLD_VALUE)

    def textFromValue(self, value):
        if value == self.OFF_VALUE:
            return "Off"
        if value == self.HOLD_VALUE:
            return "Hold"
        return f"{value} ms"

    def valueFromText(self, text):
        stripped = text.strip().lower()
        if stripped == "off":
            return self.OFF_VALUE
        if stripped == "hold":
            return self.HOLD_VALUE
        digits = stripped[:-2].strip() if stripped.endswith("ms") else stripped
        return int(digits) if digits.isdigit() else self.value()

    def validate(self, text, pos):
        stripped = text.strip().lower()
        if stripped == "":
            return (QValidator.State.Intermediate, text, pos)
        if stripped in ("off", "hold"):
            return (QValidator.State.Acceptable, text, pos)
        if "off".startswith(stripped) or "hold".startswith(stripped):
            return (QValidator.State.Intermediate, text, pos)
        digits = stripped[:-2].strip() if stripped.endswith("ms") else stripped
        if digits.isdigit():
            if self.minimum() <= int(digits) <= self.maximum():
                return (QValidator.State.Acceptable, text, pos)
            return (QValidator.State.Intermediate, text, pos)
        return (QValidator.State.Invalid, text, pos)

    def mouseDoubleClickEvent(self, event):
        self.setValue(self.HOLD_VALUE)
        event.accept()
