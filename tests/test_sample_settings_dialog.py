# tests for ui/sample_settings_dialog.py - the closest-bit-depth snapping
# (a stored bit depth might not be one of the dialog's own offered options)
# and get_settings()'s "(not set)" sentinel handling for starting sample
# number, which a Generic SDS device needs and an Akai ignores.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from ui.sample_settings_dialog import SampleSettingsDialog


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize(
    "stored_bit_depth,expected",
    [
        (16, 16),
        (8, 8),
        (11, 10),  # closer to 10 than 12
        (13, 12),  # exactly between 12 and 14 - ties go to the lower option
        (0, 8),  # below every option - clamps to the lowest
        (20, 16),  # above every option - clamps to the highest
    ],
)
def test_bit_depth_snaps_to_the_closest_offered_option(qapp, stored_bit_depth, expected):
    dialog = SampleSettingsDialog(bit_depth=stored_bit_depth)
    assert dialog.bit_depth_combo.currentData() == expected


def test_unrecognised_sample_rate_falls_back_to_original(qapp):
    # a rate this dialog doesn't offer (e.g. a file's own odd native rate)
    # must fall back to "Original" (None) rather than erroring or landing
    # on some arbitrary nearby option - sample rate isn't safely "closest"
    # the way bit depth is (resampling isn't free)
    dialog = SampleSettingsDialog(sample_rate=12345)
    assert dialog.rate_combo.currentData() is None


def test_get_settings_reports_no_starting_slot_when_left_unset(qapp):
    dialog = SampleSettingsDialog(show_starting_slot=True, starting_sample_number=None)
    assert dialog.get_settings()["starting_sample_number"] is None


def test_get_settings_reports_an_explicit_starting_slot(qapp):
    dialog = SampleSettingsDialog(show_starting_slot=True, starting_sample_number=5)
    assert dialog.get_settings()["starting_sample_number"] == 5


def test_get_settings_reports_none_when_starting_slot_isnt_shown(qapp):
    dialog = SampleSettingsDialog(show_starting_slot=False)
    assert dialog.get_settings()["starting_sample_number"] is None


def test_get_settings_reports_no_name_when_name_field_is_hidden(qapp):
    # the shared dialog is reused for the global settings dialog, which has
    # no per-sample name to edit
    dialog = SampleSettingsDialog(show_name=False)
    assert dialog.get_settings()["name"] is None
