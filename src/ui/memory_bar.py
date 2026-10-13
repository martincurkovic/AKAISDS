"""The sampler's sample-memory bar: one widget for the Transfer Dashboard, the Program Editor's Samples tab and its Global tab.

`info` is the dict `SamplerController.memory_status_updated` and `BridgeWorker.memory_status_loaded` both emit:
`max_num_samp_words`, `num_words_free`, `num_blocks_free`.
"""

from PySide6.QtWidgets import QProgressBar

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


class MemoryBar(QProgressBar):
    """A QProgressBar that starts out saying it hasn't read the sampler yet."""

    UNKNOWN_FORMAT = "memory: -"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setValue(0)
        self.set_unknown()

    def set_status(self, info):
        self.setFormat("%p% memory used")
        apply_memory_status(self, info)

    def set_unknown(self, reason=""):
        """Nothing (valid) read: an empty bar saying so, so a stale figure is never left up."""
        self.setFormat(self.UNKNOWN_FORMAT)
        self.setProperty("memoryLevel", "normal")
        self.setValue(0)
        self.setToolTip(reason)
        self.style().unpolish(self)
        self.style().polish(self)
