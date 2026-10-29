"""Lets a control with a fixed width grow to fit its text when a theme's font is wider than the one the layouts were made for.

The editors pin many labels, buttons and combo boxes to a width in pixels ("Velocity Low" at 80 px) that fits the 13 px system font. The
Macintosh System 6 theme's bitmap font is 16 px and wider, so those clipped ("Velocity Lou"). Only a theme that asks for it (palette key
`fit_fixed_widths`) is touched: the other themes keep every number as it was. One application-level filter (`install`, called from
`theme.apply_to_app`) acts when a widget is shown: a width pinned below what the widget says it needs is raised to that.

The same filter gives the lists whose rows are widgets (a QListWidget with `setItemWidget`) an inverted selection in a theme that asks for it
(`invert_selected_rows`): the stylesheet colours an item's own text, but not the labels inside a row widget, so the selected row's widget gets
the dynamic property `rowSelected` and the stylesheet turns its labels white. The property is only set when the theme asks for it.

Wider text also makes the editors' pages wider than their windows, which then open with a horizontal scroll bar. In a theme that asks for it
(`grow_window_for_scroll`) a window whose page needs more width is made that much wider, up to the screen, and its minimum width is raised to
match (`grow_window`): the minimums the windows work out when they are built are measured before the theme's font is applied. The check re-runs
after every layout change of a scroll area or its page, so a vertical scroll bar appearing late is caught too.
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
    """Make the window holding `scroll_area` wide enough for its page: widen it by what the horizontal scroll bar lacks, no wider than the screen, and
    raise its MINIMUM width to that too (a window sized by hand before the theme's font was applied has a minimum that is now too small: the user
    could shrink it back into a scroll bar). True if it changed the window."""
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
    if window.minimumWidth() < width:
        window.setMinimumWidth(width)
    window.resize(width, window.height())
    return True


def _scroll_area_of(widget):
    """The QScrollArea `widget` is (or is the page of), or None."""
    if isinstance(widget, QScrollArea):
        return widget
    viewport = widget.parentWidget()
    area = viewport.parentWidget() if viewport is not None else None
    return area if isinstance(area, QScrollArea) and area.widget() is widget else None


def _schedule_grow(scroll_area) -> None:
    # (debounced: a page reaches its final width over several layout passes - the font, the fitted widths, a vertical scroll bar appearing -
    # and a tab's page again when its tab is first shown, so this runs after any of them, not once at a fixed time)
    if scroll_area.property("growPending"):
        return
    scroll_area.setProperty("growPending", True)

    def run(area=scroll_area):
        if shiboken6.isValid(area):
            area.setProperty("growPending", False)
            grow_window(area)

    QTimer.singleShot(30, run)


def _watch_scroll_area(scroll_area) -> None:
    if scroll_area.property("growWatched"):
        return
    scroll_area.setProperty("growWatched", True)
    scroll_area.installEventFilter(_filter)
    if scroll_area.widget() is not None:
        scroll_area.widget().installEventFilter(_filter)


class _FitFilter(QObject):
    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Type.Resize, QEvent.Type.LayoutRequest):
            area = _scroll_area_of(obj) if isinstance(obj, QWidget) else None
            if area is not None and area.property("growWatched") and theme.current_palette().get("grow_window_for_scroll"):
                _schedule_grow(area)
            return False
        if kind != QEvent.Type.Show:
            return False
        if isinstance(obj, QScrollArea):
            if theme.current_palette().get("grow_window_for_scroll"):
                _watch_scroll_area(obj)
                _schedule_grow(obj)
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
