import os
import re

from PySide6.QtGui import (
    QColor,
    QKeySequence,
    QPixmap,
    QShortcut,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_HELP_DIR = os.path.join(os.path.dirname(__file__), "help")
_QUICKSTART_PATH = os.path.join(_HELP_DIR, "quickstart.md")

# screenshots ship at their native resolution (up to 1370px wide - see
# src/ui/help/screenshots/) since that's what the README wants on GitHub;
# QTextDocument doesn't auto-fit images to the viewport, so this dialog
# caps each one down after render rather than shipping separately-resized
# copies just for the in-app view - one set of source images either way
_MAX_IMAGE_WIDTH = 640

# fixed (not theme-derived) highlight colors, same idea as a browser's
# find-in-page: a plain QTextCursor selection was tried first and was
# unreadably faint in both light and dark mode, because QTextBrowser only
# paints its FULL selection color while it actually has keyboard focus -
# and focus normally sits in search_input while searching, so every match
# rendered in Qt's dim "inactive" selection tint instead. QTextEdit.
# ExtraSelection paints regardless of focus, so these are used for the
# visible highlight instead of the real text cursor's own selection.
_MATCH_BACKGROUND = QColor("#f5c518")
_MATCH_FOREGROUND = QColor("#1a1a1a")
_CURRENT_MATCH_BACKGROUND = QColor("#ff8f00")
_CURRENT_MATCH_FOREGROUND = QColor("#1a1a1a")


def _github_heading_slug(text):
    # matches GitHub's own auto-generated heading anchors exactly (lower-
    # case, non-word/non-space/non-hyphen characters dropped rather than
    # replaced with anything, THEN spaces -> hyphens) - deliberately not
    # collapsing runs of hyphens: "Sending & Receiving" drops the "&" but
    # keeps both spaces around where it was, giving "sending--receiving"
    # (double hyphen), same as GitHub's own renderer would produce, and
    # same as quickstart.md's own internal links are already written
    # against - so the same `#some-heading` syntax works unmodified both
    # on GitHub and in this dialog.
    return re.sub(r"[^\w\s-]", "", text.lower()).replace(" ", "-")


class QuickStartDialog(QDialog):
    """Renders help/quickstart.md - the same file linked from the README's
    own Quick Start section (see README.md), so there is exactly one copy
    of this content to keep in sync, not a duplicate baked into the app.
    Bundled into packaged builds via pysidedeploy.spec's
    --include-data-dir=ui/help=ui/help, same mechanism as ui/icons and
    ui/style.qss.template.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Start Guide")
        self.resize(760, 640)
        self._search_matches = []
        self._current_match_index = -1

        self.viewer = QTextBrowser()
        self.viewer.setOpenExternalLinks(True)
        # resolves the markdown's relative image paths (screenshots/*.png)
        # against help/, the directory quickstart.md itself lives in
        self.viewer.setSearchPaths([_HELP_DIR])
        try:
            with open(_QUICKSTART_PATH, "r", encoding="utf-8") as f:
                self.viewer.setMarkdown(f.read())
            self._constrain_image_widths()
            self._add_heading_anchors()
        except OSError as e:
            self.viewer.setPlainText(f"Couldn't load the Quick Start guide: {e}")

        self.search_bar = self._build_search_bar()
        self.search_bar.setVisible(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search_bar)
        layout.addWidget(self.viewer)

        # StandardKey.Find resolves to the platform convention (Cmd+F on
        # macOS, Ctrl+F elsewhere) rather than hardcoding one or the other
        find_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        find_shortcut.activated.connect(self._open_search_bar)
        close_search_shortcut = QShortcut(QKeySequence("Escape"), self)
        close_search_shortcut.activated.connect(self._close_search_bar)

    def _constrain_image_widths(self):
        # QTextImageFormat renders at the image's native pixel size unless
        # a width/height is set explicitly - walk every image fragment
        # markdown produced and cap the oversized ones, preserving aspect
        # ratio. Loads each pixmap directly off disk (not via
        # document.resource(), which may not have populated its cache yet
        # at this point) purely to read its natural size.
        document = self.viewer.document()
        block = document.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                fragment = it.fragment()
                char_format = fragment.charFormat()
                if fragment.isValid() and char_format.isImageFormat():
                    image_format = char_format.toImageFormat()
                    pixmap = QPixmap(os.path.join(_HELP_DIR, image_format.name()))
                    if not pixmap.isNull() and pixmap.width() > _MAX_IMAGE_WIDTH:
                        scale = _MAX_IMAGE_WIDTH / pixmap.width()
                        image_format.setWidth(_MAX_IMAGE_WIDTH)
                        image_format.setHeight(pixmap.height() * scale)
                        cursor = QTextCursor(document)
                        cursor.setPosition(fragment.position())
                        cursor.setPosition(
                            fragment.position() + fragment.length(),
                            QTextCursor.MoveMode.KeepAnchor,
                        )
                        cursor.setCharFormat(image_format)
                it += 1
            block = block.next()

    def _add_heading_anchors(self):
        # `[link](#some-heading)` renders fine out of setMarkdown() (Qt
        # does create the <a href="#..."> fragment), but nothing on the
        # HEADING side matches it - Qt's own Markdown importer never
        # assigns an id/name to headings the way GitHub's renderer does,
        # confirmed by inspecting toHtml() after setMarkdown(): no id/name
        # attribute anywhere near a heading block. QTextBrowser.
        # scrollToAnchor()/its own click handling both work fine once a
        # matching named anchor actually exists (verified directly) - so
        # this backfills exactly that, using the same slug GitHub would
        # generate for each heading, rather than inventing a different
        # convention that would only work in this dialog.
        document = self.viewer.document()
        block = document.begin()
        while block.isValid():
            if block.blockFormat().headingLevel() > 0 and block.text():
                slug = _github_heading_slug(block.text())
                cursor = QTextCursor(document)
                cursor.setPosition(block.position())
                cursor.setPosition(
                    block.position() + len(block.text()),
                    QTextCursor.MoveMode.KeepAnchor,
                )
                anchor_format = QTextCharFormat()
                anchor_format.setAnchor(True)
                anchor_format.setAnchorNames([slug])
                cursor.mergeCharFormat(anchor_format)
            block = block.next()

    def _build_search_bar(self):
        bar = QWidget()
        # otherwise this picks up style.qss.template's global
        # `QWidget { background-color: ${bg} }` rule, which reads as a
        # distinct box sitting on top of the dialog rather than blending
        # into it - same fix as program_editor_window.py's own bare
        # QWidget containers (see AGENTS.md)
        bar.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 6)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Find in page...")
        self.search_input.textChanged.connect(self._on_search_text_changed)
        self.search_input.returnPressed.connect(self._find_next)

        self.search_results_label = QLabel("")
        self.search_results_label.setFixedWidth(70)

        prev_button = QPushButton("←")
        prev_button.setToolTip("Previous match")
        prev_button.clicked.connect(self._find_previous)

        next_button = QPushButton("→")
        next_button.setToolTip("Next match")
        next_button.clicked.connect(self._find_next)

        close_button = QPushButton("×")
        close_button.setToolTip("Close (Esc)")
        close_button.clicked.connect(self._close_search_bar)

        layout.addWidget(self.search_input)
        layout.addWidget(self.search_results_label)
        layout.addWidget(prev_button)
        layout.addWidget(next_button)
        layout.addWidget(close_button)
        return bar

    def _open_search_bar(self):
        self.search_bar.setVisible(True)
        self.search_input.setFocus()
        self.search_input.selectAll()
        if self.search_input.text():
            self._on_search_text_changed(self.search_input.text())

    def _close_search_bar(self):
        if not self.search_bar.isVisible():
            return
        self.search_bar.setVisible(False)
        self.viewer.setExtraSelections([])
        self.viewer.setFocus()

    def _on_search_text_changed(self, text):
        self._search_matches = self._find_all_matches(text) if text else []
        self._current_match_index = 0 if self._search_matches else -1
        if self._search_matches:
            self._go_to_match(self._current_match_index)
        else:
            self.viewer.setExtraSelections([])
            self._update_results_label()

    def _find_all_matches(self, text):
        matches = []
        document = self.viewer.document()
        cursor = QTextCursor(document)
        while True:
            cursor = document.find(text, cursor)
            if cursor.isNull():
                break
            matches.append((cursor.selectionStart(), cursor.selectionEnd()))
        return matches

    def _go_to_match(self, index):
        self._current_match_index = index
        start, _end = self._search_matches[index]
        # scrolls the viewport to the match - a plain, non-selecting cursor
        # position, since the actual highlight is painted by
        # _apply_highlights' ExtraSelections instead (see their own
        # comment on why the real selection isn't used for this)
        cursor = self.viewer.textCursor()
        cursor.setPosition(start)
        self.viewer.setTextCursor(cursor)
        self.viewer.ensureCursorVisible()
        self._apply_highlights()
        self._update_results_label()

    def _apply_highlights(self):
        selections = []
        for i, (start, end) in enumerate(self._search_matches):
            cursor = QTextCursor(self.viewer.document())
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            char_format = QTextCharFormat()
            is_current = i == self._current_match_index
            char_format.setBackground(
                _CURRENT_MATCH_BACKGROUND if is_current else _MATCH_BACKGROUND
            )
            char_format.setForeground(
                _CURRENT_MATCH_FOREGROUND if is_current else _MATCH_FOREGROUND
            )
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = char_format
            selections.append(selection)
        self.viewer.setExtraSelections(selections)

    def _find_next(self):
        if not self._search_matches:
            return
        self._current_match_index = (self._current_match_index + 1) % len(
            self._search_matches
        )
        self._go_to_match(self._current_match_index)

    def _find_previous(self):
        if not self._search_matches:
            return
        self._current_match_index = (self._current_match_index - 1) % len(
            self._search_matches
        )
        self._go_to_match(self._current_match_index)

    def _update_results_label(self):
        if not self.search_input.text():
            self.search_results_label.setText("")
        elif not self._search_matches:
            self.search_results_label.setText("0 of 0")
        else:
            total = len(self._search_matches)
            self.search_results_label.setText(
                f"{self._current_match_index + 1} of {total}"
            )


def show_quickstart_dialog(parent):
    # one instance reused across Help menu clicks rather than a fresh
    # dialog each time - re-showing/raising an already-open guide is more
    # useful than stacking duplicate windows, and non-modal (.show(), not
    # .exec()) so it can stay open alongside the window it's explaining
    dialog = getattr(parent, "_quickstart_dialog", None)
    if dialog is None:
        dialog = QuickStartDialog(parent)
        parent._quickstart_dialog = dialog
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
