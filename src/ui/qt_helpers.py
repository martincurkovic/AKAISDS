from PySide6.QtGui import QFontMetrics, QPixmap, QPainter, QColor
from PySide6.QtCore import Qt, QRectF, QEvent
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QTabBar


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
        if parent is not None and self.count() > 0:
            size.setWidth(parent.width() // self.count())
        return size


def widen_popup_to_fit_items(combo):
    # ugh, so qcombobox's dropdown popup defaults to the same width as the closed combo box
    # this checksthe length of all of the options and sets that to the width...
    metrics = QFontMetrics(combo.font())
    widest = max(
        (metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())),
        default=0,
    )
    combo.view().setMinimumWidth(widest + 40)  # giving extra padding just in case


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
