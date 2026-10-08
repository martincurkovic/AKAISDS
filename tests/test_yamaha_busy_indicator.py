# tests for the A4000 editor's loading bar: the same indeterminate bar, beside Refresh, with the same show/hide debounce as the
# S3000 editor's (shown after 200 ms of being busy, hidden 150 ms after the session goes idle). Real window + session vs FakeA4000.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QProgressBar

from test_s950_transfers import qapp, wait_until  # noqa: F401
from test_yamaha_program_editor import fake, win  # noqa: F401


def settle(ms):
    """Let the event loop (and the debounce timers) run for `ms`."""
    from time import monotonic

    end = monotonic() + ms / 1000
    while monotonic() < end:
        QCoreApplication.processEvents()


def test_the_bar_sits_beside_refresh_and_starts_hidden(win):
    bar = win._loading_progress
    assert isinstance(bar, QProgressBar)
    assert (bar.minimum(), bar.maximum()) == (0, 0)  # indeterminate, like the S3000 editor's
    assert bar.minimumWidth() == bar.maximumWidth() == 120
    assert not bar.isTextVisible()
    row = [b for b in win.centralWidget().layout().children() if b.indexOf(win.refresh_button) >= 0][0]
    # Refresh, then the (hidden unless something is cancellable) Cancel button, then the bar - the S3000 editor's order
    assert row.indexOf(win.cancel_button) == row.indexOf(win.refresh_button) + 1
    assert row.indexOf(bar) == row.indexOf(win.cancel_button) + 1
    assert wait_until(bar.isHidden, timeout=2)  # (the fixture returns inside the scan's 150 ms hide grace period)


def test_a_refresh_is_one_busy_span_that_ends_idle(win):
    changes = []
    win._session.busy_changed.connect(changes.append)
    win._refresh()
    assert wait_until(lambda: len(win._counts) == 128 and not win._scanning, timeout=20)
    assert wait_until(lambda: not win._session.working)
    assert changes and changes[0] is True and changes[-1] is False
    assert all(a != b for a, b in zip(changes, changes[1:]))  # only real transitions are reported


def test_a_wave_load_is_not_a_loading_bar_event(win):
    changes = []
    win._session.busy_changed.connect(changes.append)
    win.fake.audio["sine wave"] = list(range(-300, 300))
    got = []
    win._session.request_wave("sine wave", got.append)
    assert wait_until(lambda: bool(got), timeout=10)
    assert not win._session.working and changes == []


def test_the_bar_shows_after_200ms_and_hides_150ms_after_idle(win):
    bar = win._loading_progress
    settle(250)
    assert bar.isHidden()
    win._on_session_busy_changed(True)
    settle(100)
    assert bar.isHidden()  # a quick read never flashes it
    assert wait_until(lambda: not bar.isHidden(), timeout=2)
    win._on_session_busy_changed(False)
    settle(60)
    assert not bar.isHidden()  # still in the grace period
    assert wait_until(bar.isHidden, timeout=2)


def test_a_new_busy_during_the_hide_grace_period_keeps_the_bar_up(win):
    bar = win._loading_progress
    win._on_session_busy_changed(True)
    assert wait_until(lambda: not bar.isHidden(), timeout=2)
    win._on_session_busy_changed(False)
    settle(50)
    win._on_session_busy_changed(True)  # the next read of a burst
    settle(250)
    assert not bar.isHidden()
    win._on_session_busy_changed(False)
    assert wait_until(bar.isHidden, timeout=2)


def test_a_stale_idle_while_the_session_is_working_again_does_not_hide_the_bar(win, monkeypatch):
    bar = win._loading_progress
    win._on_session_busy_changed(True)
    assert wait_until(lambda: not bar.isHidden(), timeout=2)
    monkeypatch.setattr(type(win._session), "working", property(lambda self: True))
    win._on_session_busy_changed(False)
    settle(300)
    assert not bar.isHidden()
    monkeypatch.undo()
    win._confirm_session_idle()
    assert bar.isHidden()


def test_closing_the_window_hides_the_bar_and_unhooks_it(win):
    win._on_session_busy_changed(True)
    assert wait_until(lambda: not win._loading_progress.isHidden(), timeout=2)
    win.close()
    assert win._loading_progress.isHidden()
    win._session.busy_changed.emit(True)  # (a later session event must not touch the closed window)
    settle(300)
    assert win._loading_progress.isHidden()
