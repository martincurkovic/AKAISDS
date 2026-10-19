# ui/wheel_guard.py: scrolling a page never changes a control it passes over; only a clicked control answers the wheel.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ui import wheel_guard
from ui.knob import Knob
from ui.note_spinbox import NoteSpinBox


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def page(qapp):
    """A scroll area, much taller than its window, with one of each control in it."""
    guard = wheel_guard.install(qapp)
    controls = {
        "spin": QSpinBox(),
        "double": QDoubleSpinBox(),
        "note": NoteSpinBox(),
        "combo": QComboBox(),
        "slider": QSlider(Qt.Orientation.Horizontal),
        "knob": Knob(),
    }
    controls["spin"].setRange(0, 100)
    controls["double"].setRange(0, 100)
    controls["combo"].addItems(["a", "b", "c", "d"])
    controls["slider"].setRange(0, 100)
    controls["knob"].setRange(0, 100)
    controls["knob"].setEnabled(True)
    controls["knob"].setFixedSize(40, 40)
    inner = QWidget()
    layout = QVBoxLayout(inner)
    for control in controls.values():
        layout.addWidget(control)
        layout.addSpacing(300)  # (so the page really scrolls)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(inner)
    area.resize(300, 400)
    area.show()
    QApplication.processEvents()
    area.controls, area.guard = controls, guard
    yield area
    area.close()
    area.deleteLater()
    wheel_guard.uninstall(qapp)


def value_of(control):
    return control.currentIndex() if isinstance(control, QComboBox) else control.value()


def wheel(control, delta=120):
    event = QWheelEvent(
        QPointF(control.rect().center()),
        QPointF(control.mapToGlobal(control.rect().center())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(control, event)
    QApplication.processEvents()
    return event


def click(control):
    # (QTest hands the click to the widget it is given; a real one lands on the spin box's text part)
    target = control.lineEdit() if isinstance(control, (QSpinBox, QDoubleSpinBox)) else control
    QTest.mouseClick(target, Qt.MouseButton.LeftButton, pos=target.rect().center())
    if isinstance(control, QComboBox):
        control.hidePopup()  # (the click opened the drop-down; the wheel is for the closed combo)
    QApplication.processEvents()


def leave(control):
    QApplication.sendEvent(control, QEvent(QEvent.Type.Leave))


def enter(control):
    center = QPointF(control.rect().center())
    QApplication.sendEvent(control, QEnterEvent(center, center, center))


CONTROLS = ["spin", "double", "note", "combo", "slider", "knob"]


@pytest.mark.parametrize("name", CONTROLS)
def test_a_wheel_tick_over_an_unclicked_control_scrolls_the_page_and_leaves_the_value(page, name):
    control = page.controls[name]
    page.ensureWidgetVisible(control)
    QApplication.processEvents()
    bar = page.verticalScrollBar()
    bar.setValue(bar.value() + 50)  # (room to scroll both ways)
    before_value, before_scroll = value_of(control), bar.value()
    wheel(control, -120)
    assert value_of(control) == before_value
    assert bar.value() > before_scroll


@pytest.mark.parametrize("name", CONTROLS)
def test_a_clicked_control_takes_the_wheel_and_the_page_stays_put(page, name):
    control = page.controls[name]
    page.ensureWidgetVisible(control)
    QApplication.processEvents()
    bar = page.verticalScrollBar()
    bar.setValue(bar.value() + 50)
    click(control)
    assert page.guard.armed_control() is control and control.property("armed") is True
    before_value, before_scroll = value_of(control), bar.value()
    tick = -120 if isinstance(control, QComboBox) else 120  # (a combo's wheel goes down the list, the others' up in value)
    wheel(control, tick)
    wheel(control, tick)
    assert value_of(control) != before_value
    assert bar.value() == before_scroll


@pytest.mark.parametrize("name", CONTROLS)
def test_the_cursor_coming_and_going_does_not_disarm_it(page, name):
    control = page.controls[name]
    page.ensureWidgetVisible(control)
    click(control)
    leave(control)
    enter(control)
    leave(control)
    assert page.guard.armed_control() is control and control.property("armed") is True
    tick = -120 if isinstance(control, QComboBox) else 120
    before = value_of(control)
    wheel(control, tick)
    assert value_of(control) != before


def test_one_control_is_armed_at_a_time_and_a_click_elsewhere_disarms(page):
    spin, combo = page.controls["spin"], page.controls["combo"]
    click(spin)
    click(combo)
    assert page.guard.armed_control() is combo and not spin.property("armed")
    click(page.widget())  # (the page itself)
    assert page.guard.armed_control() is None


def test_clicking_the_text_part_of_a_spin_box_arms_the_spin_box(page):
    spin = page.controls["spin"]
    line_edit = spin.lineEdit()
    QTest.mouseClick(line_edit, Qt.MouseButton.LeftButton, pos=line_edit.rect().center())
    assert page.guard.armed_control() is spin


def test_scroll_bars_always_scroll(page):
    bar = page.verticalScrollBar()
    before = bar.value()
    wheel(bar, -120)
    assert bar.value() > before


def test_keyboard_focus_is_not_taken_away_when_the_cursor_leaves(page):
    spin = page.controls["spin"]
    click(spin)
    spin.setFocus()
    leave(spin)
    assert spin.hasFocus()


def test_the_wheel_does_nothing_special_without_the_guard(qapp):
    wheel_guard.uninstall(qapp)
    spin = QSpinBox()
    spin.setRange(0, 100)
    spin.show()
    wheel(spin, 120)
    assert spin.value() == 1  # (the old behaviour: any tick changes it)
    spin.close()
    spin.deleteLater()


def test_install_is_idempotent(qapp):
    first = wheel_guard.install(qapp)
    assert wheel_guard.install(qapp) is first
    wheel_guard.uninstall(qapp)


# -- a combo's drop-down, and what else disarms ---------------------------------------------------------------------


def test_a_combo_stays_armed_through_its_drop_down(page):
    combo = page.controls["combo"]
    click(combo)
    combo.showPopup()
    leave(combo)  # (the drop-down takes the cursor off the combo)
    QApplication.processEvents()
    combo.hidePopup()
    enter(combo)  # (and Qt sends this once it is gone)
    QApplication.processEvents()
    assert page.guard.armed_control() is combo
    before = combo.currentIndex()
    wheel(combo, -120)
    assert combo.currentIndex() != before


def test_picking_an_item_in_the_drop_down_arms_the_combo(page):
    combo = page.controls["combo"]
    combo.showPopup()
    QApplication.processEvents()
    view = combo.view().viewport()
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=view.rect().center())
    QApplication.processEvents()
    assert page.guard.armed_control() is combo


def test_hiding_the_armed_control_disarms_it(page):
    spin = page.controls["spin"]
    click(spin)
    assert page.guard.armed_control() is spin
    spin.hide()
    QApplication.processEvents()
    assert page.guard.armed_control() is None and not spin.property("armed")
    spin.show()
    assert page.guard.armed_control() is None


# -- the knob's outline: four square corner marks around the dial -----------------------------------------------------


def test_the_armed_marks_are_four_square_corners_of_the_knob_rectangle():
    from PySide6.QtCore import QRectF

    from ui.knob import armed_corner_marks

    lines = armed_corner_marks(QRectF(0, 0, 40, 40), length=6, inset=1)
    assert len(lines) == 8
    corners = {(round(line.x1()), round(line.y1())) for line in lines}
    assert corners == {(1, 1), (39, 1), (1, 39), (39, 39)}
    for line in lines:
        assert line.dx() == 0 or line.dy() == 0  # (square: horizontal or vertical strokes only)
        assert abs(line.dx()) + abs(line.dy()) == 6
        # every stroke runs inward from its corner, so it stays inside the rectangle
        for x, y in ((line.x1(), line.y1()), (line.x2(), line.y2())):
            assert 0 <= x <= 40 and 0 <= y <= 40


def test_a_tiny_knob_gets_shorter_marks_that_do_not_meet():
    from PySide6.QtCore import QRectF

    from ui.knob import armed_corner_marks

    lines = armed_corner_marks(QRectF(0, 0, 12, 12), length=6, inset=0)
    assert all(abs(line.dx()) + abs(line.dy()) <= 4 for line in lines)


def accent_pixels(knob):
    from PySide6.QtGui import QColor

    from ui import theme

    image = knob.grab().toImage()
    accent = QColor(theme.current_palette()["accent"])
    ratio = image.devicePixelRatio()

    def is_accent(x, y):
        pixel = QColor(image.pixelColor(int(x * ratio), int(y * ratio)))
        return abs(pixel.red() - accent.red()) + abs(pixel.green() - accent.green()) + abs(pixel.blue() - accent.blue()) < 90

    return is_accent


def test_an_armed_knob_paints_corner_marks_and_no_ring(qapp):
    knob = Knob()
    knob.setRange(0, 100)
    knob.setEnabled(True)
    knob.setFixedSize(48, 48)
    knob.show()
    knob.setProperty("armed", True)
    knob.update()
    QApplication.processEvents()
    is_accent = accent_pixels(knob)
    assert is_accent(0, 4) and is_accent(4, 0)  # the top-left mark, both strokes
    assert is_accent(47, 4) and is_accent(43, 0)  # the top-right
    assert is_accent(0, 43) and is_accent(4, 47)  # the bottom-left
    assert is_accent(47, 43) and is_accent(43, 47)  # the bottom-right
    assert not is_accent(24, 0) and not is_accent(0, 24)  # the middle of an edge: no full outline
    knob.setProperty("armed", False)
    knob.update()
    QApplication.processEvents()
    assert not accent_pixels(knob)(0, 4)
    knob.close()
    knob.deleteLater()
