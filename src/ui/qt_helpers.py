from PySide6.QtGui import QFontMetrics, QPixmap, QPainter, QColor
from PySide6.QtCore import Qt, QRectF, QEvent
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QTabBar,
    QVBoxLayout,
    QWidget,
)


class FullWidthTabBar(QTabBar):
    # QTabBar's own "expanding" property only redistributes width *within*
    # whatever space the bar already claimed for itself (its natural,
    # content-sized width) - it never stretches the bar to fill its parent
    # QTabWidget, so tabs end up left-aligned with dead space to the right.
    # Overriding tabSizeHint to divide the QTabWidget's actual width evenly
    # is the standard workaround. Must be installed via
    # `tab_widget.setTabBar(FullWidthTabBar(tab_widget))` BEFORE any tabs
    # are added - swapping the tab bar afterward drops the existing tabs.
    #
    # QTabWidget only ever sizes its tab bar once, when tabs are first laid
    # out - a later window resize changes the QTabWidget's own width but
    # never re-asks the bar for a new sizeHint, so the bar (and its tabs)
    # stay frozen at their original width forever. Watching the QTabWidget
    # for resize events and explicitly resizing the bar to match is what
    # makes the tabs actually track the window width - that resize is what
    # triggers the bar's own internal re-layout (via tabSizeHint) at the
    # new size.
    def __init__(self, tab_widget):
        super().__init__(tab_widget)
        tab_widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.resize(watched.width(), self.height())
        return super().eventFilter(watched, event)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        parent = self.parentWidget()
        # only VISIBLE tabs share the width - a hidden tab (the Program
        # Editor hides its Multi tab in S1000 mode) still counts in count(),
        # and dividing by that left the remaining tabs short of the full
        # width with dead space on the right
        visible = [i for i in range(self.count()) if self.isTabVisible(i)]
        if parent is not None and index in visible:
            count = len(visible)
            position = visible.index(index)
            # plain floor division drops up to (count - 1) px total, which
            # very visibly falls short of the content pane's width for most
            # window sizes - handing the leftover pixels to the last few
            # tabs keeps the bar's total width exactly matching parent.width()
            total = parent.width()
            base, remainder = divmod(total, count)
            width = base + (1 if position >= count - remainder else 0)
            size.setWidth(width)
        return size


def align_form_label_columns(*forms):
    # every QFormLayout sizes its OWN label column to its own widest label,
    # so a page of several section cards (one form each) ends up with the
    # entry fields starting at a different x in every card - "Theme:" is
    # short, "Device ID (SysEx ch. 0-127):" is not. Giving every label in
    # every form the width of the widest one lines the field columns up
    # across the whole page. Rows with no label (a note spanning the full
    # width) are skipped. Call after every row has been added, and before
    # anything reads the page's sizeHint (the dialog measures it for its
    # minimum width).
    labels = []
    for form in forms:
        for row in range(form.rowCount()):
            item = form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            widget = item.widget() if item is not None else None
            if isinstance(widget, QLabel):
                labels.append(widget)
    if not labels:
        return
    width = max(label.sizeHint().width() for label in labels)
    for label in labels:
        label.setMinimumWidth(width)


def widen_popup_to_fit_items(combo):
    # ugh, so qcombobox's dropdown popup defaults to the same width as the closed combo box
    # this checksthe length of all of the options and sets that to the width...
    metrics = QFontMetrics(combo.font())
    widest = max(
        (metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())),
        default=0,
    )
    combo.view().setMinimumWidth(widest + 40)  # giving extra padding just in case


def build_section_card(title, *row_layouts):
    # groups related rows into one visually distinct card - same surface/
    # border look as the Program Editor's own keygroup zone card (see
    # QWidget#sectionCard in style.qss.template). Originally
    # ProgramEditorWindow._build_section_card (see AGENTS.md's "section
    # cards, scroll areas" notes for the full history of the gotchas
    # below) - moved here, unchanged, once ui/settings_dialog.py needed the
    # exact same behaviour rather than a second hand-rolled copy of it.
    header = QLabel(title)
    header.setObjectName("sectionHeader")

    section_layout = QVBoxLayout()
    section_layout.setContentsMargins(12, 10, 12, 12)
    section_layout.setSpacing(10)
    section_layout.addWidget(header)
    for row in row_layouts:
        section_layout.addLayout(row)
    # without this, a card whose content is shorter than the row it's
    # paired with gets its leftover height split BEFORE the header too, not
    # just after the content - QBoxLayout distributes surplus space evenly
    # across every gap when nothing claims a stretch, which reads as the
    # whole card being vertically centered rather than top-aligned like a
    # taller neighbor. This claims all of it at the bottom instead.
    section_layout.addStretch()

    card = QWidget()
    card.setObjectName("sectionCard")
    card.setLayout(section_layout)
    # a bare QWidget defaults to Preferred vertically, which CAN grow past
    # its own sizeHint when the surrounding layout has surplus space to
    # hand out - a trailing addStretch() alone isn't enough to stop this
    # (its own default stretch factor of 0 doesn't outrank a sibling
    # Preferred-policy widget's equal willingness to grow), so without this
    # a card can grow taller than its content as its surrounding page
    # grows. Fixed vertically pins each card to its sizeHint - can't grow
    # OR compress below it.
    card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return card


def build_scroll_area(page):
    # wraps a whole tab/page rather than adding it directly - lets the
    # window's minimum height stay comfortable without needing to grow
    # every time a section card is added, at the cost of a scrollbar on a
    # short window instead of everything always fitting unscrolled. Same
    # reasoning as ProgramEditorWindow._build_scroll_area, moved here for
    # ui/settings_dialog.py to share rather than duplicate.
    scroll_area = QScrollArea()
    scroll_area.setWidgetResizable(True)
    scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll_area.setWidget(page)
    return scroll_area


def load_colored_pixmap(svg_path, color, size=16, scale=3):
    # render SVG icon and recolor EVERY opaque pixel to "color"
    # regardless of whatever color the svg itself was drawn in
    renderer = QSvgRenderer(svg_path)
    physical_size = size * scale
    pixmap = QPixmap(physical_size, physical_size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, physical_size, physical_size))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(color))
    painter.end()

    pixmap.setDevicePixelRatio(scale)
    return pixmap
