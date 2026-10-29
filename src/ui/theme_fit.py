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

A theme switched WHILE windows are open (Settings > Appearance) is handled by `refit_all`: widgets already shown get no Show event, so everything is fitted again when the theme changes, and the widths this module replaced are put back when the theme is left.
"""

import weakref

import shiboken6
from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QDialog,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QWidget,
)

from ui import theme

_FITTED = (QLabel, QPushButton, QComboBox, QAbstractSpinBox)
_UNLIMITED = 16777215

_filter = None

# Every widget this module acts on, once it has been shown (weak: a closed window drops out). `refit_all` walks THIS, never QApplication.allWidgets():
# that returns a snapshot of raw pointers, and the garbage collector can delete a parentless widget (a window a test or a dialog left behind) while the
# snapshot is still being wrapped, which crashed (segmentation fault) inside the loop. A widget never shown needs nothing: it is fitted when shown.
_seen = weakref.WeakSet()


def _remember_seen(widget) -> None:
    try:
        _seen.add(widget)
    except TypeError:  # (not weak-referenceable: nothing to track)
        pass


def _live():
    return [w for w in list(_seen) if shiboken6.isValid(w)]


def fit(widget) -> bool:
    """Raise a width that is pinned (or capped) below the widget's size hint to the size hint. True if it changed anything."""
    if isinstance(widget, QLabel) and (widget.wordWrap() or not widget.pixmap().isNull()):
        return False
    needed = widget.sizeHint().width()
    lo, hi = widget.minimumWidth(), widget.maximumWidth()
    changed = False
    if lo == hi and 0 < hi < needed:  # fixed
        _remember_widths(widget, lo, hi)
        widget.setFixedWidth(needed)
        changed = True
    elif lo < hi < _UNLIMITED and hi < needed:  # capped
        _remember_widths(widget, lo, hi)
        widget.setMaximumWidth(needed)
        changed = True
    return changed


def _remember_widths(widget, lo, hi) -> None:
    """Keep the widths the widget was built with, so leaving the theme can put them back."""
    if widget.property("fitOrigMin") is None:
        widget.setProperty("fitOrigMin", lo)
        widget.setProperty("fitOrigMax", hi)


def restore(widget) -> bool:
    """Put back the widths `fit` replaced. True if there was something to put back."""
    lo, hi = widget.property("fitOrigMin"), widget.property("fitOrigMax")
    if lo is None:
        return False
    widget.setMinimumWidth(lo)
    widget.setMaximumWidth(hi)
    widget.setProperty("fitOrigMin", None)
    widget.setProperty("fitOrigMax", None)
    return True


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
        if window.property("fitOrigMinWidth") is None:
            window.setProperty("fitOrigMinWidth", window.minimumWidth())
        window.setMinimumWidth(width)
    window.resize(width, window.height())
    return True


def grow_for_layout(window) -> bool:
    """Make a window at least as wide as its own layout needs. A window that pins its minimum size in code (the Dashboard: 1030 px) keeps that minimum
    even when the theme's wider buttons and labels need more, and Qt lets the explicit minimum win over the layout's, so labels got cut. Raises the
    minimum too, and widens the window, no wider than the screen. True if it changed the window."""
    if window is None or not window.isVisible() or window.isMaximized() or window.isFullScreen():
        return False
    needed = window.minimumSizeHint().width()
    if needed <= window.minimumWidth() and needed <= window.width():
        return False
    screen = window.screen()
    needed = min(needed, screen.availableGeometry().width()) if screen is not None else needed
    changed = False
    if window.minimumWidth() < needed:
        if window.property("fitOrigMinWidth") is None:
            window.setProperty("fitOrigMinWidth", window.minimumWidth())
        window.setMinimumWidth(needed)
        changed = True
    if window.width() < needed:
        window.resize(needed, window.height())
        changed = True
    return changed


def _grow_later(window) -> None:
    for delay in (0, 150):
        QTimer.singleShot(delay, lambda w=window: shiboken6.isValid(w) and grow_for_layout(w))


def group(name, *widgets) -> None:
    """Declare widgets that must stay the same width as one another (a column of cells and its header, two stacked buttons). In a theme that fits
    widths, each is first fitted to its own text and then all are widened to the widest of them: fitting one at a time would let them drift apart."""
    for widget in widgets:
        widget.setProperty("fitGroup", name)


def _need(widget) -> int:
    """The width a widget asks for now (a container: what its layout asks for)."""
    return max(widget.sizeHint().width(), widget.minimumSizeHint().width())


def fit_groups() -> None:
    groups = {}
    for widget in _live():
        if widget.property("fitGroup") is not None:
            groups.setdefault((id(widget.window()), widget.property("fitGroup")), []).append(widget)
    for members in groups.values():
        for member in members:
            member.ensurePolished()
        fixed = [m for m in members if m.minimumWidth() == m.maximumWidth() > 0]
        if not fixed:
            continue
        target = max(max(_need(m) for m in fixed), max(m.minimumWidth() for m in fixed))
        for member in fixed:
            if member.minimumWidth() < target:
                _remember_widths(member, member.minimumWidth(), member.maximumWidth())
                member.setFixedWidth(target)


def _fit_groups_later() -> None:
    # (after the group's members have been shown and fitted, which happens a moment after the first of them is shown)
    for delay in (0, 150):
        QTimer.singleShot(delay, fit_groups)


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


def resize_rows(list_widget) -> None:
    """Give each row of a list built from row widgets the height its widget needs NOW: the lists set each item's size hint from its widget when the row is
    built, so rows built under another theme's font keep that theme's height (the buttons in them are cut off)."""
    for i in range(list_widget.count()):
        item = list_widget.item(i)
        row = list_widget.itemWidget(item)
        if row is not None:
            row.ensurePolished()
            if row.layout() is not None:
                row.layout().invalidate()
            item.setSizeHint(row.sizeHint())


def refit_all() -> None:
    """Bring every widget in line with the theme now in effect. Widgets already on screen when the theme changes get no Show event, so a switch to
    System 6 would leave their widths (and the Settings dialog's minimum width) as the old theme's font made them: fit them all, watch every scroll
    area, mark the row lists. A switch away puts back the widths this module replaced."""
    palette = theme.current_palette()
    fit_on = bool(palette.get("fit_fixed_widths"))
    grow_on = bool(palette.get("grow_window_for_scroll"))
    rows_on = bool(palette.get("invert_selected_rows"))
    for widget in _live():
        if isinstance(widget, _FITTED):
            if fit_on:
                widget.ensurePolished()
                fit(widget)
            else:
                restore(widget)
        elif isinstance(widget, QScrollArea) and grow_on:
            _watch_scroll_area(widget)
            _schedule_grow(widget)
        elif isinstance(widget, (QMainWindow, QDialog)) and widget.isWindow() and grow_on:
            grow_for_layout(widget)
        elif isinstance(widget, QListWidget):
            resize_rows(widget)
            if rows_on:
                if not widget.property("rowSelectionTracked"):
                    widget.setProperty("rowSelectionTracked", True)
                    widget.itemSelectionChanged.connect(lambda lw=widget: sync_row_selection(lw))
                sync_row_selection(widget)
        if not fit_on and widget.property("fitGroup") is not None:
            restore(widget)
        if not grow_on and widget.isWindow() and widget.property("fitOrigMinWidth") is not None:
            widget.setMinimumWidth(widget.property("fitOrigMinWidth"))
            widget.setProperty("fitOrigMinWidth", None)
    if fit_on:
        fit_groups()


def _theme_changed() -> None:
    # (after the application style sheet has been applied and the widgets re-polished; again a little later for what lays out late)
    for delay in (0, 150):
        QTimer.singleShot(delay, refit_all)


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
        if isinstance(obj, QWidget):
            if isinstance(obj, (*_FITTED, QScrollArea, QListWidget)) or obj.isWindow() or obj.property("fitGroup") is not None:
                _remember_seen(obj)
            if obj.property("fitGroup") is not None and theme.current_palette().get("fit_fixed_widths"):
                _fit_groups_later()
        if isinstance(obj, QScrollArea):
            if theme.current_palette().get("grow_window_for_scroll"):
                _watch_scroll_area(obj)
                _schedule_grow(obj)
            return False
        if isinstance(obj, (QMainWindow, QDialog)) and obj.isWindow():
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
        theme.notifier.changed.connect(_theme_changed)
    return _filter
