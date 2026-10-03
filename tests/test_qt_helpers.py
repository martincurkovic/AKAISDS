# tests for ui/qt_helpers.py's FullWidthTabBar - the tab-width distribution
# math it uses to make tabs fill the whole tab widget (see its own
# docstring: plain floor division drops up to (count - 1) px, which is
# very visibly short of the pane's width for most window sizes) and the
# resize-tracking that keeps it that way after the window is resized.

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from ui.qt_helpers import FullWidthTabBar


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _tab_widget_with_full_width_bar(qapp, tab_count, width):
    tab_widget = QTabWidget()
    tab_widget.setTabBar(FullWidthTabBar(tab_widget))
    for i in range(tab_count):
        tab_widget.addTab(QWidget(), f"Tab {i}")
    tab_widget.resize(width, 200)
    tab_widget.show()
    qapp.processEvents()
    return tab_widget


def _tab_widths(tab_widget):
    bar = tab_widget.tabBar()
    return [bar.tabRect(i).width() for i in range(bar.count())]


def test_tabs_exactly_fill_the_tab_widgets_width(qapp):
    # a width not evenly divisible by the tab count is the case plain floor
    # division gets wrong - 310 / 3 = 103.33..., which floor division would
    # leave short by 1px total
    tab_widget = _tab_widget_with_full_width_bar(qapp, tab_count=3, width=310)

    assert sum(_tab_widths(tab_widget)) == 310


def test_leftover_pixels_go_to_the_last_tabs_not_the_first(qapp):
    tab_widget = _tab_widget_with_full_width_bar(qapp, tab_count=3, width=310)

    widths = _tab_widths(tab_widget)
    assert widths[0] == widths[1]
    assert widths[2] == widths[0] + 1


def test_tabs_still_exactly_fill_the_width_when_it_divides_evenly(qapp):
    tab_widget = _tab_widget_with_full_width_bar(qapp, tab_count=4, width=400)

    assert _tab_widths(tab_widget) == [100, 100, 100, 100]


def test_resizing_the_tab_widget_after_the_fact_relayouts_the_tabs(qapp):
    # QTabWidget only ever sizes its tab bar once, when tabs are first laid
    # out - FullWidthTabBar's eventFilter is what makes a later window
    # resize actually reach the tabs at all
    tab_widget = _tab_widget_with_full_width_bar(qapp, tab_count=3, width=310)

    tab_widget.resize(400, 200)
    qapp.processEvents()

    assert sum(_tab_widths(tab_widget)) == 400


def test_hidden_tabs_dont_take_a_share_of_the_width(qapp):
    # the Program Editor hides its Multi tab in S1000 mode - the two tabs
    # left must still span the whole width, not two thirds of it
    tab_widget = _tab_widget_with_full_width_bar(qapp, tab_count=3, width=300)
    tab_widget.setTabVisible(0, False)
    qapp.processEvents()

    bar = tab_widget.tabBar()
    visible_widths = [bar.tabRect(i).width() for i in (1, 2)]
    assert sum(visible_widths) == 300
    assert visible_widths == [150, 150]


def test_hidden_tab_leftover_pixels_still_go_to_the_last_visible_tabs(qapp):
    tab_widget = _tab_widget_with_full_width_bar(qapp, tab_count=4, width=301)
    tab_widget.setTabVisible(1, False)
    qapp.processEvents()

    bar = tab_widget.tabBar()
    widths = [bar.tabRect(i).width() for i in (0, 2, 3)]
    assert sum(widths) == 301
    assert widths == sorted(widths)  # remainder went to the later tabs


def test_hidden_tabs_still_track_a_resize(qapp):
    tab_widget = _tab_widget_with_full_width_bar(qapp, tab_count=3, width=300)
    tab_widget.setTabVisible(0, False)
    tab_widget.resize(400, 200)
    qapp.processEvents()

    bar = tab_widget.tabBar()
    assert sum(bar.tabRect(i).width() for i in (1, 2)) == 400
