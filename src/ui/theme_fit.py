"""Lets a control with a fixed width grow to fit its text when a theme's font is wider than the one the layouts were made for.

The editors pin many labels, buttons and combo boxes to a width in pixels ("Velocity Low" at 80 px) that fits the 13 px system font. The
Macintosh System 6 theme's bitmap font is 16 px and wider, so those clipped ("Velocity Lou"). Only a theme that asks for it (palette key
`fit_fixed_widths`) is touched: the other themes keep every number as it was. One application-level filter (`install`, called from
`theme.apply_to_app`) acts when a widget is shown: a width pinned below what the widget says it needs is raised to that.

The same filter gives the lists whose rows are widgets (a QListWidget with `setItemWidget`) an inverted selection in a theme that asks for it
(`invert_selected_rows`): the stylesheet colours an item's own text, but not the labels inside a row widget, so the selected row's widget gets
the dynamic property `rowSelected` and the stylesheet turns its labels white. The property is only set when the theme asks for it.

Wider text also makes the editors' pages wider than their windows, which then open with a horizontal scroll bar. In a theme that asks for it
(`grow_window_for_scroll`) a window whose page needs more width is made that much wider, up to the screen (`grow_window`).
"""

import shiboken6
from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox, QLabel, QListWidget, QPushButton, QScrollArea, QWidget

from ui import theme

_FITTED = (QLabel, QPushButton, QComboBox, QAbstractSpinBox)
_UNLIMITED = 16777215

_filter = None


def fit(widget) -> bool:
    """Raise a width that is pinned (or capped) below the widget's size hint to the size hint. True if it changed anything."""
    if isinstance(widget, QLabel) and (widget.wordWrap() or not widget.pixmap().isNull()):
        return False
    needed = widget.sizeHint().width()
    lo, hi = widget.minimumWidth(), widget.maximumWidth()
    changed = False
    if lo == hi and 0 < hi < needed:  # fixed
        widget.setFixedWidth(needed)
        changed = True
    elif lo < hi < _UNLIMITED and hi < needed:  # capped
        widget.setMaximumWidth(needed)
        changed = True
    return changed


def _repolish(widget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


def sync_row_selection(list_widget) -> None:
    """Mark each row widget of `list_widget` as selected or not (property `rowSelected`), so the stylesheet can invert its labels."""
    for i in range(list_widget.count()):
        item = list_widget.item(i)
        row = list_widget.itemWidget(item)
        if row is None or row.property("rowSelected") == item.isSelected():
            continue
        row.setProperty("rowSelected", item.isSelected())
        _repolish(row)
        for child in row.findChildren(QWidget):
            _repolish(child)


def grow_window(scroll_area) -> bool:
    """Widen the window holding `scroll_area` by what its horizontal scroll bar lacks, no wider than the screen. True if it changed the window."""
    window = scroll_area.window()
    if window is None or window.isMaximized() or window.isFullScreen() or not window.isVisible():
        return False
    missing = scroll_area.horizontalScrollBar().maximum()
    if missing <= 0:
        return False
    screen = window.screen()
    limit = screen.availableGeometry().width() if screen is not None else window.width() + missing
    width = min(window.width() + missing, limit)
    if width <= window.width():
        return False
    window.resize(width, window.height())
    return True


def _grow_later(scroll_area) -> None:
    # (the page only has its final width once the window has laid out, and a tab's page again when its tab is first shown)
    for delay in (0, 150):
        QTimer.singleShot(delay, lambda s=scroll_area: shiboken6.isValid(s) and grow_window(s))


class _FitFilter(QObject):
    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Show:
            return False
        if isinstance(obj, QScrollArea):
            if theme.current_palette().get("grow_window_for_scroll"):
                _grow_later(obj)
            return False
        if isinstance(obj, _FITTED):
            if theme.current_palette().get("fit_fixed_widths"):
                fit(obj)
        elif isinstance(obj, QListWidget) and theme.current_palette().get("invert_selected_rows"):
            if not obj.property("rowSelectionTracked"):
                obj.setProperty("rowSelectionTracked", True)
                obj.itemSelectionChanged.connect(lambda lw=obj: sync_row_selection(lw))
            sync_row_selection(obj)
        return False


def install(app=None):
    """Install the filter on the application (once; a second call returns the first)."""
    global _filter
    app = app or QApplication.instance()
    if _filter is None or not shiboken6.isValid(_filter):
        _filter = _FitFilter(app)
        app.installEventFilter(_filter)
    return _filter
