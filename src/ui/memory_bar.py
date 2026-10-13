"""The sampler's sample-memory bar: one widget for the Transfer Dashboard, the Program Editor's Samples tab and its Global tab.

`info` is the dict `SamplerController.memory_status_updated` and `BridgeWorker.memory_status_loaded` both emit:
`max_num_samp_words`, `num_words_free`, `num_blocks_free`.
"""

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QProgressBar

from core import app_config

QWIDGETSIZE_MAX = 16777215

WARNING_PERCENT = 70
CRITICAL_PERCENT = 90


def percent_used(info):
    total = info["max_num_samp_words"]
    if total <= 0:
        return 0
    return int((total - info["num_words_free"]) / total * 100)


def level_for(percent):
    if percent >= CRITICAL_PERCENT:
        return "critical"
    if percent >= WARNING_PERCENT:
        return "warning"
    return "normal"


def apply_memory_status(bar, info):
    """Show `info` on `bar` (a QProgressBar, 0-100): the fill, the warning/critical colour (the `memoryLevel` property
    the stylesheet reads) and a tooltip with what is free."""
    percent = percent_used(info)
    bar.setProperty("memoryLevel", level_for(percent))
    bar.setValue(percent)
    bar.setToolTip(
        f"{info['num_words_free']:,} sample words free\n"
        f"{info['num_blocks_free']:,} sample blocks/slots free"
    )
    bar.style().unpolish(bar)
    bar.style().polish(bar)


class _CompactNotifier(QObject):
    # every collapsible bar (the Dashboard's and the editor's) shares one setting: a change in one reaches the others at once
    changed = Signal(bool)


_notifier = _CompactNotifier()


class MemoryBar(QProgressBar):
    """A QProgressBar that starts out saying it hasn't read the sampler yet. With `collapsible=True` a double-click shrinks it to a thin line without the
    text, and another double-click brings the full bar back (remembered in config.json); the percentage then moves to the tooltip."""

    UNKNOWN_FORMAT = "memory: -"
    THIN_HEIGHT = 8

    def __init__(self, parent=None, *, collapsible=False):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(0)
        self._collapsible = collapsible
        self._compact = False
        self._info = None
        self.set_unknown()
        if collapsible:
            self.set_compact(app_config.get_memory_bar_compact(), remember=False)
            _notifier.changed.connect(self._on_compact_changed)

    def is_compact(self):
        return self._compact

    def set_compact(self, compact, *, remember=True):
        compact = bool(compact) and self._collapsible
        self._compact = compact
        self.setTextVisible(not compact)
        if compact:
            self.setFixedHeight(self.THIN_HEIGHT)
        else:
            self.setMinimumHeight(0)
            self.setMaximumHeight(QWIDGETSIZE_MAX)
        self._refresh_tooltip()
        self.updateGeometry()
        if remember:
            app_config.save_memory_bar_compact(compact)
            _notifier.changed.emit(compact)

    def _on_compact_changed(self, compact):
        if compact != self._compact:
            self.set_compact(compact, remember=False)

    def mouseDoubleClickEvent(self, event):
        if self._collapsible and event.button() == Qt.MouseButton.LeftButton:
            self.set_compact(not self._compact)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def set_status(self, info):
        self._info = info
        self.setFormat("%p% memory used")
        apply_memory_status(self, info)
        self._refresh_tooltip()

    def set_unknown(self, reason=""):
        """Nothing (valid) read: an empty bar saying so, so a stale figure is never left up."""
        self._info = None
        self.setFormat(self.UNKNOWN_FORMAT)
        self.setProperty("memoryLevel", "normal")
        self.setValue(0)
        self.setToolTip(reason)
        self.style().unpolish(self)
        self.style().polish(self)

    def _refresh_tooltip(self):
        if self._info is None:
            return
        base = (
            f"{self._info['num_words_free']:,} sample words free\n"
            f"{self._info['num_blocks_free']:,} sample blocks/slots free"
        )
        if self._compact:
            base = f"{self.value()}% memory used\n" + base
        if self._collapsible:
            base += "\n\nDouble-click to " + ("show the full bar" if self._compact else "shrink")
        self.setToolTip(base)
