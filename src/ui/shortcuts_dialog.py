"""Help > Keyboard Shortcuts...: every shortcut the window offers, read from its own menu bar so the list can't drift from the real bindings.

Shortcuts that aren't menu actions (the Slice Editor's, the audition keyboard's keys ...) come in as extra sections built by the
`*_section()` functions below; each window passes the ones that apply to it (see `ui/standard_menus.add_help_menu`).
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QGridLayout, QLabel, QScrollArea, QStyle, QVBoxLayout, QWidget


def _native(sequence):
    return QKeySequence(sequence).toString(QKeySequence.SequenceFormat.NativeText)


def _shown(keys):
    # a dedicated Zoom In / Zoom Out media key is a real binding but not something to list
    media = (Qt.Key.Key_ZoomIn, Qt.Key.Key_ZoomOut)
    return list(dict.fromkeys(_native(k) for k in keys if not k.isEmpty() and k[0].key() not in media))


def collect_menu_shortcuts(menu_bar):
    """[(menu title, [(action text, shortcut text), ...]), ...] for every menu that has at least one shortcut."""
    sections = []
    for top_level in menu_bar.actions():
        menu = top_level.menu()
        if menu is None:
            continue
        rows = []
        _collect(menu, rows)
        if rows:
            sections.append((top_level.text().replace("&", ""), rows))
    return sections


def _collect(menu, rows):
    for action in menu.actions():
        if action.isSeparator():
            continue
        if action.menu() is not None:
            _collect(action.menu(), rows)
            continue
        keys = _shown(action.shortcuts())
        if keys:
            rows.append((action.text().replace("&", ""), " or ".join(keys)))


# -- shortcuts that aren't menu actions: (heading, [(what it does, keys), ...]) -------------------------------------------------


def audition_section():
    return (
        "Audition keyboard (while it is open)",
        [
            ("Play notes, starting at C3", "A W S E D F T G Y H U J K O L P ;"),
            ("Octave down / up", "Z / X"),
            ("Velocity down / up", "C / V"),
            ("MIDI channel down / up", "[ / ]"),
        ],
    )


def lists_section():
    return ("Program, keygroup and sample lists", [("Delete the selected item", "Delete or Backspace")])


def slice_editor_section():
    return (
        "Slice Editor",
        [
            ("Zoom in", " or ".join(_shown([QKeySequence("Ctrl+=")] + QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomIn)))),
            ("Zoom out", " or ".join(_shown(QKeySequence.keyBindings(QKeySequence.StandardKey.ZoomOut)))),
            ("Zoom to fit", _native("Ctrl+0")),
            ("Cancel the export that is running", _native("Ctrl+.")),
        ],
    )


def preview_dialogs_section():
    return ("Filter, Stretch / Pitch and Reverb dialogs", [("Start or stop the preview", "Space")])


def dragging_section():
    return (
        "Dragging",
        [
            ("Finer adjustment (markers, envelope handles, number boxes)", "Shift + drag"),
            ("Cancel an envelope handle drag", "Esc"),
        ],
    )


class ShortcutsDialog(QDialog):
    def __init__(self, parent, sections):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.sections = [(title, list(rows)) for title, rows in sections]

        body = QVBoxLayout()
        body.setContentsMargins(16, 12, 16, 12)
        body.setSpacing(14)
        for title, rows in self.sections:
            heading = QLabel(title)
            font = heading.font()
            font.setBold(True)
            heading.setFont(font)
            body.addWidget(heading)
            grid = QGridLayout()
            grid.setHorizontalSpacing(24)
            grid.setVerticalSpacing(4)
            grid.setColumnStretch(0, 1)
            for row, (what, keys) in enumerate(rows):
                grid.addWidget(QLabel(what), row, 0)
                key_label = QLabel(keys)
                key_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                key_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                grid.addWidget(key_label, row, 1)
            body.addLayout(grid)
        body.addStretch()

        page = QWidget()
        page.setLayout(body)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(page)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)
        self._fit_to_contents(page, buttons)

    def _fit_to_contents(self, page, buttons):
        # as wide as the widest row needs (plus room for the vertical scroll bar), as tall as the contents up to most of the screen
        margins = self.layout().contentsMargins()
        bar = self.style().pixelMetric(QStyle.PixelMetric.PM_ScrollBarExtent)
        content = page.sizeHint()
        width = content.width() + bar + margins.left() + margins.right()
        height = content.height() + buttons.sizeHint().height() + self.layout().spacing() + margins.top() + margins.bottom()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            height = min(height, int(screen.availableGeometry().height() * 0.85))
        self.setMinimumWidth(width)
        self.resize(width, height)


def show_shortcuts_dialog(window, extra_sections=()):
    # one instance per window, like the Quick Start guide: re-showing an open one beats stacking copies
    dialog = getattr(window, "_shortcuts_dialog", None)
    if dialog is None:
        dialog = ShortcutsDialog(window, collect_menu_shortcuts(window.menuBar()) + list(extra_sections))
        window._shortcuts_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
