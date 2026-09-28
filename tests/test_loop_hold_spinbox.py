# tests for ui/loop_hold_spinbox.py - LDWELL1's Off/Hold sentinel display
# and the double-click-to-Hold shortcut

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.loop_hold_spinbox import LoopHoldSpinBox


def _qapp():
    return QApplication.instance() or QApplication([])


class _FakeEvent:
    def accept(self):
        pass


def test_defaults_to_hold():
    _qapp()
    spinbox = LoopHoldSpinBox()
    assert spinbox.value() == LoopHoldSpinBox.HOLD_VALUE
    assert spinbox.text() == "Hold"


def test_zero_displays_as_off():
    _qapp()
    spinbox = LoopHoldSpinBox()
    spinbox.setValue(0)
    assert spinbox.text() == "Off"


def test_middle_value_displays_as_plain_ms():
    _qapp()
    spinbox = LoopHoldSpinBox()
    spinbox.setValue(1500)
    assert spinbox.text() == "1500 ms"


def test_value_from_text_round_trips():
    _qapp()
    spinbox = LoopHoldSpinBox()
    assert spinbox.valueFromText("Off") == 0
    assert spinbox.valueFromText("hold") == 9999
    assert spinbox.valueFromText("250 ms") == 250
    assert spinbox.valueFromText("250") == 250


def test_double_click_sets_hold_regardless_of_current_value():
    _qapp()
    spinbox = LoopHoldSpinBox()
    spinbox.setValue(42)
    assert spinbox.value() == 42
    spinbox.mouseDoubleClickEvent(_FakeEvent())
    assert spinbox.value() == LoopHoldSpinBox.HOLD_VALUE
