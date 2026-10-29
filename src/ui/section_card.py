"""Collapsible section cards: the editors' cards with a Finder-style disclosure arrow beside the title.

`qt_helpers.build_section_card(..., collapse_key=...)` returns a `SectionCard`; the editors reach it through `build_editor_card`. Rules worth knowing:

- Cards the editors pin to ONE height (`editor_layout.equalize_card_heights`: a pair like Loop Preview | Root Note & Tune) form a GROUP. A group collapses and
  expands together and is re-pinned to the tallest member's height each time (`pin_equal_heights`) - otherwise a short header card would sit beside a tall one.
  A lone card is `Preferred, Fixed` and simply follows its own size hint.
- Only the BODY (the rows) hides. The title row stays, and so does the arrow; the small controls beside a title (`header_widgets`) hide with the body.
- Whether a card is collapsed is remembered per card (`config.json` `collapsed_cards`, keys like `program_editor/Filter`: one entry per editor and title, so a
  title must be unique within its editor - `tests/test_section_cards.py` checks that).
- A card can ask for attention (`set_attention`): an amber dot beside the title while collapsed, for something the user must not miss (the Edit Sample card's unsent
  edits). It does not expand the card; the caller does that with `set_collapsed(False, remember=False)` (not remembered: it is not the user's choice).
- Collapsing clears the keyboard focus out of the body first: a hidden focused widget hands focus on, and a QScrollArea scrolls to it (see
  `qt_helpers.set_enabled_without_scrolling`).
"""

from PySide6.QtCore import QEvent, QPointF, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from core import app_config
from ui import theme

# the keys of the collapsed cards, read from config.json once per process and kept in step with it
_collapsed_keys = None


def _keys():
    global _collapsed_keys
    if _collapsed_keys is None:
        _collapsed_keys = set(app_config.get_collapsed_cards())
    return _collapsed_keys


def reset_state():
    """Forget the cached keys so the next card reads config.json again (tests)."""
    global _collapsed_keys
    _collapsed_keys = None


def pin_equal_heights(cards):
    """Pin every card to the tallest one's size hint. The layouts are re-activated first so a card whose body was just shown or hidden measures its NEW content."""
    for card in cards:
        if isinstance(card, SectionCard):
            card.refresh_layouts()
            continue
        layout = card.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
    target_height = max(card.sizeHint().height() for card in cards)
    for card in cards:
        card.setFixedHeight(target_height)


class DisclosureArrow(QAbstractButton):
    """The small right/down triangle. Painted, so it follows the theme live; focused by Tab only (a click must not take focus)."""

    SIZE = 14

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapsed = False

    def set_collapsed(self, collapsed):
        self._collapsed = collapsed
        self.update()

    def keyPressEvent(self, event):
        # Space already clicks a QAbstractButton; Return/Enter should too
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.click()
            return
        super().keyPressEvent(event)

    def paintEvent(self, _event):
        palette = theme.current_palette()
        painter = QPainter(self)
        if palette.get("card_titlebar"):  # System 6: a small white box with the triangle inside, like a window's close box (hard edges: no anti-aliasing)
            box = QRect(1, 1, self.SIZE - 3, self.SIZE - 3)
            painter.fillRect(box, QColor(palette["bg"]))
            painter.setPen(QPen(QColor(palette["border"]), 1))
            painter.drawRect(box.adjusted(0, 0, -1, -1))
            c = self.SIZE / 2 - 0.5
            points = [QPointF(c - 1.5, c - 3), QPointF(c + 2.5, c), QPointF(c - 1.5, c + 3)] if self._collapsed else [QPointF(c - 3, c - 1.5), QPointF(c + 3, c - 1.5), QPointF(c, c + 2.5)]
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(palette["border"]))
            painter.drawPolygon(QPolygonF(points))
            if self.hasFocus():
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(palette["border"]), 1, Qt.PenStyle.DotLine))
                painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, theme.antialiasing())
        color = QColor(palette["text_bright"] if self.underMouse() else palette["text"])
        c = self.SIZE / 2
        if self._collapsed:  # pointing right
            points = [QPointF(c - 2.5, c - 4), QPointF(c + 3, c), QPointF(c - 2.5, c + 4)]
        else:  # pointing down
            points = [QPointF(c - 4, c - 2.5), QPointF(c + 4, c - 2.5), QPointF(c, c + 3)]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF(points))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(palette["accent"]), 1))
            painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)


class SectionCard(QWidget):
    collapsed_changed = Signal(bool)

    def __init__(self, title, rows, header_widgets=(), key=None):
        super().__init__()
        self.setObjectName("sectionCard")
        # a QWidget SUBCLASS only paints its stylesheet background/border (the card look) with this
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._title = title
        self._key = key
        self._group = ()
        self._attention = False
        self._collapsed = False
        self._header_widgets = list(header_widgets)

        self._arrow = DisclosureArrow()
        self._arrow.clicked.connect(self.toggle)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("sectionHeader")
        self._title_label.installEventFilter(self)  # a click on the title toggles too
        self._attention_dot = QLabel("●")
        self._attention_dot.setObjectName("unsentDot")  # the amber the sample list's unsent marker uses
        self._attention_dot.hide()

        header_row = QHBoxLayout()
        header_row.setSpacing(6)
        header_row.addWidget(self._arrow)
        header_row.addWidget(self._title_label)
        header_row.addWidget(self._attention_dot)
        # (the two spacers are what addSpacing(2) and addStretch() were; System 6 swaps their roles so the small controls sit at the bar's right end)
        self._header_row = header_row
        self._lead_spacer = QSpacerItem(2, 0, QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Minimum)
        self._trail_spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        header_row.addItem(self._lead_spacer)
        for widget in self._header_widgets:
            header_row.addWidget(widget)
        header_row.addItem(self._trail_spacer)

        # the rows live in a widget so they can be hidden (a layout can't be). Not the page colour inside a card; Maximum vertically so it never takes
        # the height a pair pins on a shorter card - the stretch below does, as in a plain card
        self._body = QWidget()
        self._body.setObjectName("transparentContainer")
        self._body.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        body_layout = QVBoxLayout(self._body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)
        for row in rows:
            body_layout.addLayout(row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(10)
        layout.addLayout(header_row)
        layout.addWidget(self._body)
        layout.addStretch()
        # Fixed vertically: pinned to its size hint, never grows with the page (see qt_helpers.build_section_card)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self._apply(bool(key) and key in _keys())
        theme.notifier.changed.connect(self._theme_changed)
        self._theme_changed()

    # -- state -------------------------------------------------------------------------------------

    def title(self):
        return self._title

    def _theme_changed(self):
        # System 6 draws the heading as a striped title bar flush with the card's top edge: less room above it
        titlebar = bool(theme.current_palette().get("card_titlebar"))
        self.layout().setContentsMargins(12, 3 if titlebar else 10, 12, 12)
        # in a title bar the small controls (a zoom box, like a System 6 window's) sit at its right end, as tall as the arrow
        fixed, expanding = QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding
        self._lead_spacer.changeSize(2, 0, expanding if titlebar else fixed, QSizePolicy.Policy.Minimum)
        self._trail_spacer.changeSize(0, 0, fixed if titlebar else expanding, QSizePolicy.Policy.Minimum)
        self._header_row.invalidate()
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not theme.current_palette().get("card_titlebar"):
            return
        # the System 6 title bar: horizontal black lines every other pixel, with a white patch behind the title and anything else in the heading
        palette = theme.current_palette()
        header = self.layout().itemAt(0).geometry()
        bar = QRect(1, 1, self.width() - 2, header.bottom() + 3)
        painter = QPainter(self)
        painter.setPen(QPen(QColor(palette["border"]), 1))
        for y in range(bar.top() + 1, bar.bottom(), 2):
            painter.drawLine(bar.left(), y, bar.right(), y)
        painter.fillRect(QRect(bar.left(), bar.bottom(), bar.width(), 1), QColor(palette["border"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette["bg"]))
        # one patch per item, but all as tall as the tallest (the title's label is taller than the 14 px arrow, and patches of different heights
        # left a stripe showing under the arrow and a step where they met)
        shown = [w for w in (self._title_label, self._attention_dot, self._arrow, *self._header_widgets) if w.isVisibleTo(self)]
        if shown:
            top = min(w.geometry().top() for w in shown) - 1
            bottom = max(w.geometry().bottom() for w in shown) + 1
            for widget in shown:
                margin = 2 if widget is self._arrow else 4
                geometry = widget.geometry()
                painter.drawRect(QRect(geometry.left() - margin, top, geometry.width() + 2 * margin, bottom - top + 1))

    def key(self):
        return self._key

    def is_collapsed(self):
        return self._collapsed

    def arrow(self):
        return self._arrow

    def toggle(self):
        self.set_collapsed(not self._collapsed)

    def set_height_group(self, cards):
        """The cards pinned to one height with this one (`editor_layout.equalize_card_heights`): they collapse and expand together."""
        self._group = tuple(cards)

    def set_collapsed(self, collapsed, *, remember=True):
        """Collapse or expand this card (and its height group). `remember=False` for a change the user didn't ask for."""
        if collapsed == self._collapsed:
            return
        members = [self] + [c for c in self._group if c is not self and isinstance(c, SectionCard)]
        for card in members:
            card._apply(collapsed)
        if len(self._group) > 1:
            pin_equal_heights(self._group)
        else:
            self.updateGeometry()
        if remember:
            for card in members:
                if card._key:
                    _remember(card._key, collapsed)
        for card in members:
            card.collapsed_changed.emit(collapsed)

    def set_attention(self, on, tooltip=""):
        """An amber dot beside the title while the card is collapsed (something inside needs the user's eye)."""
        self._attention = bool(on)
        self._attention_dot.setToolTip(tooltip)
        self._attention_dot.setVisible(self._attention and self._collapsed)

    def refresh_layouts(self):
        """Drop the cached size hints of the card AND its body: widgets hidden inside it while the window isn't shown (the S1000 gating) leave stale hints in the body's
        own layout, which the card's layout doesn't know about."""
        self._body.layout().invalidate()
        self._body.layout().activate()
        self.layout().invalidate()
        self.layout().activate()

    def _apply(self, collapsed):
        if collapsed:
            focused = QApplication.focusWidget()
            if focused is not None and self._body.isAncestorOf(focused):
                focused.clearFocus()
        self._collapsed = collapsed
        self._body.setVisible(not collapsed)
        for widget in self._header_widgets:
            widget.setVisible(not collapsed)
        self._arrow.set_collapsed(collapsed)
        self._arrow.setToolTip(f"{'Expand' if collapsed else 'Collapse'} {self._title}")
        self._arrow.setAccessibleName(f"{'Expand' if collapsed else 'Collapse'} {self._title}")
        self._attention_dot.setVisible(self._attention and collapsed)
        self.refresh_layouts()

    def eventFilter(self, watched, event):
        if (
            watched is self._title_label
            and event.type() == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and self._title_label.rect().contains(event.position().toPoint())
        ):
            self.toggle()
        return super().eventFilter(watched, event)


def _remember(key, collapsed):
    keys = _keys()
    if collapsed:
        keys.add(key)
    else:
        keys.discard(key)
    app_config.save_collapsed_cards(keys)
