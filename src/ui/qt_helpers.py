from PySide6.QtGui import QFontMetrics, QPixmap, QPainter, QColor
from PySide6.QtCore import Qt, QRectF
from PySide6.QtSvg import QSvgRenderer


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
