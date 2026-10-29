"""Black-and-white message box icons for the Macintosh System 6 theme, painted here (no files, hard edges)."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap, QPolygonF

from . import theme


def warning_pixmap(size: int = 48, dpr: float = 1.0) -> QPixmap:
    """The warning triangle of a question dialog: an outlined triangle with a bar and a dot."""
    palette = theme.current_palette()
    ink, paper = QColor(palette["text"]), QColor(palette["bg_panel"])
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    unit = size / 48
    painter.scale(unit, unit)
    pen = painter.pen()
    pen.setColor(ink)
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(paper)
    painter.drawPolygon(QPolygonF([QPointF(24, 4), QPointF(45, 43), QPointF(3, 43)]))
    painter.fillRect(QRectF(22, 16, 4, 14), ink)
    painter.fillRect(QRectF(22, 34, 4, 4), ink)
    painter.end()
    return pm


def _message_pixmap(kind: str, size: int) -> QPixmap:
    """A message box icon at `size` px: "information" (a circle with an i), "question" (a ?), "critical" (an octagon with an X)."""
    palette = theme.current_palette()
    ink, paper = QColor(palette["text"]), QColor(palette["bg_panel"])
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.scale(size / 48, size / 48)
    pen = painter.pen()
    pen.setColor(ink)
    pen.setWidth(2)
    painter.setPen(pen)
    painter.setBrush(paper)
    if kind == "critical":
        points = [(15, 3), (33, 3), (45, 15), (45, 33), (33, 45), (15, 45), (3, 33), (3, 15)]
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        pen.setWidth(4)
        painter.setPen(pen)
        painter.drawLine(QPointF(16, 16), QPointF(32, 32))
        painter.drawLine(QPointF(32, 16), QPointF(16, 32))
    else:
        painter.drawEllipse(QRectF(3, 3, 42, 42))
        if kind == "information":
            painter.fillRect(QRectF(22, 20, 5, 17), ink)
            painter.fillRect(QRectF(22, 11, 5, 5), ink)
        else:
            font = QFont(painter.font())
            font.setPixelSize(34)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(QRectF(3, 3, 42, 42), Qt.AlignmentFlag.AlignCenter, "?")
    painter.end()
    return pm


def message_icon(kind: str) -> QIcon:
    """The icon for a QMessageBox ("information", "question", "warning" or "critical"), at the sizes it is asked for."""
    icon = QIcon()
    for size in (16, 32, 48, 64, 96):
        icon.addPixmap(warning_pixmap(size) if kind == "warning" else _message_pixmap(kind, size))
    return icon
