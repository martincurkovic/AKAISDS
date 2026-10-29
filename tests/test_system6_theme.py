# Tests for the Macintosh System 6 theme (ui/theme.py SYSTEM6_PALETTE, ui/style_system6.qss.template, ui/theme_fit.py).
#
# Never restyles the real QApplication (that is what made the suite crawl, see AGENTS.md): `_StandInApp` takes the style sheet and palette
# and keeps them. Pixel checks assert what the picture shows, not font-dependent sizes.

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QPalette, QTextCursor
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QListWidgetItem, QTextBrowser, QStyle, QWidget

from core import app_config
from ui import mono_icons, theme, theme_fit, update_dialog

_UI = os.path.dirname(theme.__file__)
_REPO = os.path.dirname(os.path.dirname(_UI))


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _StandInApp:
    """Takes the place of the QApplication where only theme.py's own logic is under test (a real setStyleSheet restyles every live widget)."""

    def __init__(self, qapp):
        self._qapp = qapp
        self._sheet = ""
        self._palette = QPalette()

    def setStyle(self, style):
        pass

    def style(self):
        return self._qapp.style()

    def setStyleSheet(self, sheet):
        self._sheet = sheet

    def styleSheet(self):
        return self._sheet

    def setPalette(self, palette):
        self._palette = palette

    def palette(self):
        return self._palette


@pytest.fixture
def s6(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(theme, "_GENERATED_ICONS_DIR", str(tmp_path / "icons"))
    saved = (
        theme._active_palette, theme._app, theme._preference, theme._connected_app, theme._style_app, theme._qt_palette_overridden,
    )
    theme._active_palette = None
    theme._connected_app = None
    app = _StandInApp(qapp)
    theme.apply_to_app(app, "system6")
    yield app
    QGuiApplication.styleHints().unsetColorScheme()
    (theme._active_palette, theme._app, theme._preference, theme._connected_app, theme._style_app, theme._qt_palette_overridden) = saved


def _sheet(monkeypatch, tmp_path, palette):
    monkeypatch.setattr(theme, "_GENERATED_ICONS_DIR", str(tmp_path / "icons"))
    return theme.render_stylesheet(palette)


# -- the palette and the stylesheet -------------------------------------------------------------------------------------------------


def test_system6_is_a_theme_choice():
    assert ("Macintosh System 6", "system6") in theme.CHOICES
    assert app_config.THEME_SYSTEM6 in app_config.THEME_VALUES


def test_the_palette_has_every_key_the_other_palettes_have():
    assert set(theme.DARK_PALETTE) <= set(theme.SYSTEM6_PALETTE)
    assert set(theme.LIGHT_PALETTE) <= set(theme.SYSTEM6_PALETTE)


def test_it_renders_with_no_placeholder_left_and_balanced_braces(monkeypatch, tmp_path):
    sheet = _sheet(monkeypatch, tmp_path, theme.SYSTEM6_PALETTE)
    assert "${" not in sheet
    code = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)  # (a "Could not parse application stylesheet" leaves the OLD sheet in place)
    assert code.count("{") == code.count("}")


def test_a_key_only_the_extra_template_needs_fails_loudly_if_missing(monkeypatch, tmp_path):
    palette = dict(theme.SYSTEM6_PALETTE)
    del palette["text_selected"]
    with pytest.raises(KeyError, match="text_selected"):
        _sheet(monkeypatch, tmp_path, palette)


def test_dark_and_light_never_see_the_extra_rules(monkeypatch, tmp_path):
    for palette in (theme.DARK_PALETTE, theme.LIGHT_PALETTE):
        sheet = _sheet(monkeypatch, tmp_path, palette)
        assert "ChiKareGo2" not in sheet and "s6_" not in sheet and "monoText" not in sheet


def test_every_corner_is_square_except_push_buttons_and_radio_buttons(monkeypatch, tmp_path):
    sheet = _sheet(monkeypatch, tmp_path, theme.SYSTEM6_PALETTE)
    radii = {int(r) for r in re.findall(r"border-radius:\s*(\d+)px", sheet)}
    assert radii <= {0, 6, 8}


def test_every_icon_the_extra_template_names_exists(monkeypatch, tmp_path):
    sheet = _sheet(monkeypatch, tmp_path, theme.SYSTEM6_PALETTE)
    paths = re.findall(r"url\(([^)]+)\)", sheet)
    assert paths
    for path in paths:
        assert os.path.exists(path), path


def test_the_scroll_track_tile_has_black_outline_columns_and_a_checker(monkeypatch, tmp_path):
    from PySide6.QtGui import QImage

    path = theme._write_checker("track_v.png", "vertical")
    image = QImage(path)
    assert (image.width(), image.height()) == (16, 2)
    black, white = QColor("#000000"), QColor("#ffffff")
    for y in range(2):
        assert image.pixelColor(0, y) == black and image.pixelColor(15, y) == black
        assert {image.pixelColor(x, y).name() for x in range(1, 15)} == {black.name(), white.name()}


def test_the_font_ships_and_loads(qapp):
    assert os.path.exists(os.path.join(_UI, "fonts", "ChiKareGo2.ttf"))
    assert os.path.exists(os.path.join(_UI, "fonts", "README.txt"))
    theme._load_bundled_fonts()
    assert "ChiKareGo2" in QFontDatabase.families()


def test_the_font_is_credited_in_the_notices():
    with open(os.path.join(_UI, "help", "THIRD_PARTY_NOTICES.md"), encoding="utf-8") as f:
        notices = f.read()
    assert "ChiKareGo2" in notices and "Giles Booth" in notices and "CC BY" in notices


@pytest.mark.parametrize("script", ["scripts/build_macos.sh", "scripts/build_linux.sh", "scripts/build_windows.ps1"])
def test_every_build_script_ships_the_extra_template_and_the_font(script):
    with open(os.path.join(_REPO, script), encoding="utf-8") as f:
        text = f.read()
    assert "ui/style_system6.qss.template=ui/style_system6.qss.template" in text
    assert "--include-data-dir=ui/fonts=ui/fonts" in text


# -- choosing it ------------------------------------------------------------------------------------------------------------------


def test_it_is_pinned_by_the_preference_whatever_the_os_says(s6, qapp):
    assert theme.current_palette() is theme.SYSTEM6_PALETTE
    assert theme.current_preference() == "system6"
    assert theme.SYSTEM6_PALETTE["bg"] in s6.styleSheet()
    assert "ChiKareGo2" in s6.styleSheet()


def test_leaving_it_gives_qt_its_own_palette_back(s6):
    assert s6.palette().color(QPalette.ColorRole.Highlight) == QColor("#000000")
    theme.set_theme_preference("dark")
    assert theme.current_palette() is theme.DARK_PALETTE
    assert "ChiKareGo2" not in s6.styleSheet()
    assert s6.palette().color(QPalette.ColorRole.Highlight) != QColor("#000000")


def test_painted_widgets_are_hard_edged_only_in_system6(s6):
    assert theme.antialiasing() is False
    theme.set_theme_preference("light")
    assert theme.antialiasing() is True


def test_the_saved_theme_round_trips():
    app_config.save_theme("system6")
    assert app_config.get_saved_theme() == "system6"


def test_neighbouring_keygroup_greys_always_differ_a_lot():
    greys = [QColor(theme.SYSTEM6_PALETTE[f"keygroup_color_{n}"]).lightness() for n in range(1, 9)]
    assert all(abs(a - b) >= 60 for a, b in zip(greys, greys[1:] + greys[:1]))
    assert all(QColor(theme.SYSTEM6_PALETTE[f"keygroup_color_{n}"]).saturation() == 0 for n in range(1, 9))


def _colours_drawn(widget):
    widget.show()
    QApplication.processEvents()
    image = widget.grab().toImage()
    return {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())}


def test_knobs_and_envelope_graphs_are_drawn_in_greys_only_in_system6(s6, qapp):
    from ui.envelope_graph import ADSREnvelopeGraph, Envelope2Graph
    from ui.knob import Knob

    knob = Knob()
    knob.setEnabled(True)  # (a knob starts disabled: until a value was read)
    knob.setRange(0, 99)
    knob.setValue(60)
    graphs = [ADSREnvelopeGraph(), Envelope2Graph()]
    for widget in [knob, *graphs]:
        widget.resize(120, 100)
        assert all(QColor(c).saturation() == 0 for c in _colours_drawn(widget)), type(widget).__name__


def test_the_other_themes_keep_the_knobs_green(s6, qapp):
    from ui.knob import Knob

    theme.set_theme_preference("dark")
    knob = Knob()
    knob.setEnabled(True)
    knob.setRange(0, 99)
    knob.setValue(60)
    knob.resize(120, 100)
    assert any(QColor(c).saturation() > 80 for c in _colours_drawn(knob))


# -- message box icons ----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["information", "question", "warning", "critical"])
def test_message_box_icons_are_black_and_white_only(s6, qapp, kind):
    image = mono_icons.message_icon(kind).pixmap(48, 48).toImage()
    seen = {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height()) if image.pixelColor(x, y).alpha() == 255}
    assert all(QColor(c).saturation() == 0 for c in seen)  # (no colour)
    if kind != "question":  # (the ? is text, so it has anti-aliased greys)
        assert seen == {"#000000", "#ffffff"}


def test_the_proxy_style_hands_out_the_mono_icons_only_while_the_theme_is_on(s6, qapp):
    style = theme._Style()
    pm = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical).pixmap(48, 48).toImage()
    assert {pm.pixelColor(x, y).name() for x in range(48) for y in range(48) if pm.pixelColor(x, y).alpha() == 255} == {"#000000", "#ffffff"}
    theme.set_theme_preference("dark")
    plain = style.standardIcon(QStyle.StandardPixmap.SP_MessageBoxCritical).pixmap(48, 48).toImage()
    assert len({plain.pixelColor(x, y).name() for x in range(48) for y in range(48)}) > 2


def test_the_style_is_installed_once_per_app(s6, qapp):
    calls = []
    app = _StandInApp(qapp)
    app.setStyle = calls.append
    theme.apply_to_app(app, "system6")
    theme.apply_to_app(app, "dark")
    assert len(calls) == 1  # (a new style object restyles every widget)


# -- ui/theme_fit.py ----------------------------------------------------------------------------------------------------------------------


def test_a_fixed_width_too_narrow_for_the_text_grows_to_fit(qapp):
    label = QLabel("A fairly long label for a narrow box")
    label.setFixedWidth(20)
    assert theme_fit.fit(label) is True
    assert label.width() >= 0 and label.maximumWidth() == label.sizeHint().width() and label.minimumWidth() == label.maximumWidth()


def test_a_wide_enough_or_free_width_is_left_alone(qapp):
    wide = QLabel("x")
    wide.setFixedWidth(500)
    free = QLabel("some text")
    assert theme_fit.fit(wide) is False and wide.maximumWidth() == 500
    assert theme_fit.fit(free) is False and free.maximumWidth() == 16777215


def test_a_capped_width_is_raised_to_the_size_hint(qapp):
    label = QLabel("A fairly long label for a narrow box")
    label.setMaximumWidth(20)
    assert theme_fit.fit(label) is True
    assert label.maximumWidth() == label.sizeHint().width()


def test_a_wrapping_label_is_never_fitted(qapp):
    label = QLabel("A fairly long label that wraps")
    label.setWordWrap(True)
    label.setFixedWidth(20)
    assert theme_fit.fit(label) is False


def test_the_filter_fits_on_show_in_system6_and_not_in_dark(s6, qapp):
    theme_fit.install(qapp)
    label = QLabel("A fairly long label for a narrow box")
    label.setFixedWidth(20)
    label.show()
    qapp.processEvents()
    assert label.maximumWidth() > 20
    label.close()
    theme.set_theme_preference("dark")
    other = QLabel("A fairly long label for a narrow box")
    other.setFixedWidth(20)
    other.show()
    qapp.processEvents()
    assert other.maximumWidth() == 20
    other.close()


def _list_with_row_widgets(qapp):
    lw = QListWidget()
    rows = []
    for text in ("one", "two"):
        item = QListWidgetItem()
        row = QWidget()
        QLabel(text, row)
        lw.addItem(item)
        lw.setItemWidget(item, row)
        rows.append(row)
    return lw, rows


def test_a_selected_row_widget_is_marked_so_its_labels_can_turn_white(s6, qapp):
    lw, rows = _list_with_row_widgets(qapp)
    theme_fit.sync_row_selection(lw)
    assert [r.property("rowSelected") for r in rows] == [False, False]
    lw.setCurrentRow(1)
    theme_fit.sync_row_selection(lw)
    assert [r.property("rowSelected") for r in rows] == [False, True]
    lw.setCurrentRow(0)
    theme_fit.sync_row_selection(lw)
    assert [r.property("rowSelected") for r in rows] == [True, False]


def test_the_filter_keeps_a_list_in_step_with_its_selection(s6, qapp):
    theme_fit.install(qapp)
    lw, rows = _list_with_row_widgets(qapp)
    lw.show()
    qapp.processEvents()
    lw.setCurrentRow(1)
    assert rows[1].property("rowSelected") is True
    lw.setCurrentRow(0)
    assert rows[1].property("rowSelected") is False
    lw.close()


def test_the_stylesheet_turns_a_selected_rows_labels_white(monkeypatch, tmp_path):
    sheet = _sheet(monkeypatch, tmp_path, theme.SYSTEM6_PALETTE)
    assert re.search(r'QWidget\[rowSelected="true"\] QLabel[^{]*\{\s*color:\s*#ffffff', sheet)


def test_a_window_is_widened_by_what_its_scroll_bar_lacks_but_not_past_the_screen(qapp):
    from PySide6.QtWidgets import QHBoxLayout, QScrollArea, QVBoxLayout

    window = QWidget()
    outer = QVBoxLayout(window)
    scroll = QScrollArea()
    page = QWidget()
    page.setMinimumWidth(500)
    scroll.setWidget(page)
    outer.addWidget(scroll)
    window.resize(300, 200)
    window.show()
    qapp.processEvents()
    missing = scroll.horizontalScrollBar().maximum()
    assert missing > 0
    screen_width = window.screen().availableGeometry().width()
    before = window.width()
    assert theme_fit.grow_window(scroll) is True
    assert window.width() == min(before + missing, screen_width)
    window.close()


# -- Markdown ---------------------------------------------------------------------------------------------------------------------------


_VIEWS = []  # (a QTextDocument is owned by its view: keep the view alive)


def _document(qapp, text):
    view = QTextBrowser()
    view.setMarkdown(text)
    _VIEWS.append(view)
    return view.document()


def _fragments(document):
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            if it.fragment().isValid():
                yield it.fragment()
            it += 1
        block = block.next()


def test_inline_code_takes_the_themes_font_and_size_in_system6(s6, qapp):
    document = _document(qapp, "text `code` text")
    update_dialog.apply_theme_to_markdown(document)
    code = [f for f in _fragments(document) if f.text() == "code"][0].charFormat()
    assert code.fontFamilies()[0] == "ChiKareGo2"
    assert not code.fontFixedPitch()
    assert code.intProperty(code.Property.FontPixelSize) == 13


def test_links_are_underlined_and_near_black_in_system6(s6, qapp):
    document = _document(qapp, "see [the site](https://example.com) now")
    update_dialog.apply_theme_to_markdown(document)
    link = [f for f in _fragments(document) if f.charFormat().isAnchor()][0].charFormat()
    assert link.fontUnderline()
    assert link.foreground().color() == QColor(theme.SYSTEM6_PALETTE["link"])


def test_other_themes_leave_a_markdown_document_alone(s6, qapp):
    theme.set_theme_preference("dark")
    document = _document(qapp, "text `code` and [a link](https://example.com)")
    before = document.toHtml()
    update_dialog.apply_theme_to_markdown(document)
    assert document.toHtml() == before
