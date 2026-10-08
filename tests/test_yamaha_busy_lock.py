# tests for the A4000 editor's lock during long operations (an audio load, an edit's or slice export's send, a restore, an assignment):
# the lists, tab bar, Refresh and the menu actions that start something are off, the controls that write are off, and a load/send can be
# cancelled from the bottom row. Real window + session vs FakeA4000.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMessageBox

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_markers_ui import user_sample
from test_yamaha_program_editor import fake, win, warning_acknowledged  # noqa: F401  (autouse: no modal warning)
from test_yamaha_waveform import window  # noqa: F401


def navigation(w):
    return [w.program_list, w.assigned_list, w.show_empty_check, w.samples_tab.sample_list_widget, w.main_tabs.tabBar(), w.refresh_button]


def actions(w):
    return [w._refresh_action, w._restore_action, w._unchanged_action, *w._tab_actions]


def test_everything_that_navigates_or_starts_something_is_off_during_a_long_operation(win):
    tab = win.samples_tab
    assert all(x.isEnabled() for x in navigation(win) + actions(win)) and win.cancel_button.isHidden()
    tab._edit_busy = True  # (what an edit's / slice export's send sets)
    tab._apply_marker_lock()
    assert not any(x.isEnabled() for x in navigation(win) + actions(win))
    assert not win.assign_button.isEnabled() and not win.remove_assigned_button.isEnabled() and not win.edit_sample_button.isEnabled()
    assert not any(w.isEnabled() for w in win.program_panel.widgets.values())  # nothing that writes
    assert not win.cancel_button.isHidden()
    tab._edit_busy = False
    tab._apply_marker_lock()
    assert all(x.isEnabled() for x in navigation(win) + actions(win)) and win.cancel_button.isHidden()
    assert win.assign_button.isEnabled() and any(w.isEnabled() for w in win.program_panel.widgets.values())


def test_a_restore_or_an_assignment_freezes_navigation_too_but_offers_no_cancel(win):
    for flag in ("_restoring", "_linking"):
        setattr(win, flag, True)
        win._apply_lock()
        assert not any(x.isEnabled() for x in navigation(win)) and win.cancel_button.isHidden(), flag
        setattr(win, flag, False)
        win._apply_lock()
        assert all(x.isEnabled() for x in navigation(win)), flag


def test_the_audio_load_freezes_the_window_and_the_samples_tabs_own_cancel_still_works(win):
    tab = win.samples_tab
    tab._loading_name = "sine wave"  # (a load in progress)
    tab._apply_marker_lock()
    assert not win.program_list.isEnabled() and not tab.sample_list_widget.isEnabled() and not win.cancel_button.isHidden()
    called = []
    tab._cancel_load = lambda: called.append(True)
    win.cancel_button.click()  # the bottom-row button reaches the load's own cancel
    assert called == [True]
    tab._loading_name = None
    tab._apply_marker_lock()
    assert win.program_list.isEnabled() and win.cancel_button.isHidden()


def test_a_real_edit_send_locks_the_window_and_the_bottom_cancel_stops_it(window, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    tab = user_sample(window, mode=4, end=4000)
    window._session.send_baud, window._session.send_gap_ms, window._session.send_tick_ms = 60_000, 0, 5  # slow enough to catch it mid-send
    window.controller._yamaha_transfers.verify_delay_ms = 0
    tab.edit_buttons["reverse"].click()
    assert wait_until(lambda: tab._edit_busy and bool(window.fake.bulk_loads), timeout=10)
    assert not window.program_list.isEnabled() and not tab.sample_list_widget.isEnabled() and not window.refresh_button.isEnabled()
    assert not window.cancel_button.isHidden() and not tab.edit_progress.isHidden()  # the progress bar is not swapped out for a placeholder
    window.cancel_button.click()
    assert wait_until(lambda: not tab._edit_busy, timeout=10)
    assert window.program_list.isEnabled() and tab.sample_list_widget.isEnabled() and window.refresh_button.isEnabled()
    assert window.cancel_button.isHidden() and "UM REV" not in window.fake.samples
