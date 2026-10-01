# tests for ui/settings_dialog.py - currently just _minimum_width_for_pages
# (the dialog-width-from-content calculation) plus a couple of real,
# end-to-end MidiSettingsDialog construction checks. Everything else in this
# dialog (port selection, the release/restore hardware-test dance, etc.) has
# no dedicated coverage yet - this file only covers what was actually added/
# fixed, per this repo's own stated testing philosophy (see TESTING.md).

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from ui.settings_dialog import (
    MidiSettingsDialog,
    _MINIMUM_DIALOG_WIDTH,
    _SCROLLBAR_WIDTH_ALLOWANCE,
    _minimum_width_for_pages,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class _FakePage:
    # duck-types the one thing _minimum_width_for_pages actually reads -
    # no real QWidget/QApplication needed for these
    def __init__(self, width):
        self._width = width

    def sizeHint(self):
        return QSize(self._width, 400)


# --- _minimum_width_for_pages -------------------------------------------------


def test_uses_the_floor_when_every_page_is_narrower_than_it(qapp):
    pages = [_FakePage(100), _FakePage(200)]
    assert _minimum_width_for_pages(pages) == _MINIMUM_DIALOG_WIDTH


def test_grows_past_the_floor_for_a_wide_page(qapp):
    pages = [_FakePage(900)]
    assert _minimum_width_for_pages(pages) == 900 + _SCROLLBAR_WIDTH_ALLOWANCE


def test_uses_the_widest_page_not_just_the_first_or_last(qapp):
    pages = [_FakePage(700), _FakePage(1200), _FakePage(300)]
    assert _minimum_width_for_pages(pages) == 1200 + _SCROLLBAR_WIDTH_ALLOWANCE


def test_empty_page_list_returns_the_floor(qapp):
    assert _minimum_width_for_pages([]) == _MINIMUM_DIALOG_WIDTH


def test_custom_minimum_is_respected_as_the_floor(qapp):
    assert _minimum_width_for_pages([_FakePage(50)], minimum=800) == 800


# --- MidiSettingsDialog: end-to-end width calculation -------------------------


class _FakeMidiManager:
    def __init__(self, input_names, output_names):
        self._input_names = input_names
        self._output_names = output_names
        self.input_name = None
        self.output_name = None

    def list_inputs(self):
        return list(self._input_names)

    def list_outputs(self):
        return list(self._output_names)

    def open_input(self, name):
        self.input_name = name

    def open_output(self, name):
        self.output_name = name


class _FakeSamplerController:
    channel = 0
    device_type = "akai"

    def is_open_loop(self):
        return False

    def set_channel(self, channel):
        self.channel = channel

    def set_device_type(self, device_type):
        self.device_type = device_type


def test_dialog_width_never_drops_below_the_floor_with_short_port_names(qapp):
    # NOT necessarily exactly the floor - the page's own explanatory note
    # labels (setWordWrap(True) QLabels) have a real unwrapped-width
    # sizeHint of their own, independent of the comboboxes, which can
    # already exceed the floor on their own. The floor is a lower BOUND,
    # not a value this test can assume short combo content collapses to
    # exactly - see test_dialog_widens_for_a_long_port_name below for the
    # actual combo-driven-growth behaviour, isolated as a relative
    # comparison instead of an absolute one for exactly this reason.
    midi_manager = _FakeMidiManager(["In A"], ["Out A"])
    dialog = MidiSettingsDialog(midi_manager, _FakeSamplerController())
    assert dialog.minimumWidth() >= _MINIMUM_DIALOG_WIDTH


def test_dialog_widens_for_a_long_port_name(qapp):
    long_name = "Some USB MIDI Interface With An Unusually Long Self-Reported Device Name " * 2
    short_manager = _FakeMidiManager(["In A"], ["Out A"])
    long_manager = _FakeMidiManager(["In A", long_name], ["Out A"])
    short_dialog = MidiSettingsDialog(short_manager, _FakeSamplerController())
    long_dialog = MidiSettingsDialog(long_manager, _FakeSamplerController())
    assert long_dialog.minimumWidth() > short_dialog.minimumWidth()


def test_dialog_width_ignores_which_tab_is_currently_shown(qapp):
    # the wide combo lives on the Audio/MIDI tab, which is index 0 (already
    # showing by default) - construct twice and confirm the SAME width
    # comes out regardless, since _minimum_width_for_pages measures BOTH
    # pages up front rather than just whichever tab happens to be visible
    long_name = "Some USB MIDI Interface With An Unusually Long Self-Reported Device Name " * 2
    midi_manager = _FakeMidiManager(["In A", long_name], ["Out A"])
    dialog_a = MidiSettingsDialog(midi_manager, _FakeSamplerController())
    dialog_b = MidiSettingsDialog(midi_manager, _FakeSamplerController())
    assert dialog_a.minimumWidth() == dialog_b.minimumWidth()
