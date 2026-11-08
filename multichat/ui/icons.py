# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Small icons drawn with QPainter.

Emoji characters depend on the system's emoji font – with the AppImage they can stay
blank (e.g. a colour font format the bundled libraries can't render). Drawn icons look
the same everywhere and need no image plugin. Shapes use a 24×24 grid.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

# colours of the role and alert icons
COLORS = {
    "broadcaster": "#e91916", "moderator": "#00ad03", "vip": "#e005b9", "subscriber": "#f0c040",
    "verified": "#9b9bff", "sub": "#bf94ff", "gift": "#bf94ff", "member": "#5fd38d", "raid": "#ffb84d",
    "bits": "#e88ef0", "superchat": "#ffcc4d",
}
DEFAULT = "#d6d6dc"


def _star(cx: float, cy: float, outer: float, inner: float) -> QPolygonF:
    import math

    pts = []
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        a = -math.pi / 2 + i * math.pi / 5
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QPolygonF(pts)


def _draw(p: QPainter, name: str, color: QColor) -> None:
    pen = QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    fill = QBrush(color)

    def poly(*xy, closed=True, filled=False):
        pg = QPolygonF([QPointF(xy[i], xy[i + 1]) for i in range(0, len(xy), 2)])
        if filled:
            p.setBrush(fill)
        (p.drawPolygon if closed else p.drawPolyline)(pg)
        p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "user":
        p.setBrush(fill)
        p.drawEllipse(QPointF(12, 8), 4, 4)
        path = QPainterPath(QPointF(4, 21))
        path.cubicTo(4, 13, 20, 13, 20, 21)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "at":
        p.drawEllipse(QPointF(12, 12), 3.5, 3.5)
        path = QPainterPath(QPointF(15.5, 9))
        path.lineTo(15.5, 13.5)
        path.cubicTo(15.5, 16.5, 20, 16.5, 20, 12)
        path.arcTo(QRectF(4, 4, 16, 16), 0, 320)
        p.drawPath(path)
    elif name == "trash":
        p.drawLine(QPointF(4, 6.5), QPointF(20, 6.5))
        poly(9.5, 6.5, 10, 3.5, 14, 3.5, 14.5, 6.5, closed=False)
        poly(6, 6.5, 7, 21, 17, 21, 18, 6.5, closed=False)
        p.drawLine(QPointF(10, 10), QPointF(10, 17.5))
        p.drawLine(QPointF(14, 10), QPointF(14, 17.5))
    elif name == "clock":
        p.drawEllipse(QPointF(12, 12), 9, 9)
        poly(12, 6.5, 12, 12, 16, 14.5, closed=False)
    elif name == "ban":
        p.drawEllipse(QPointF(12, 12), 9, 9)
        p.drawLine(QPointF(5.6, 5.6), QPointF(18.4, 18.4))
    elif name == "check":
        poly(4.5, 12.5, 9.5, 17.5, 19.5, 6.5, closed=False)
    elif name == "unban":  # circle with a check
        p.drawEllipse(QPointF(12, 12), 9, 9)
        poly(7.5, 12.5, 10.5, 15.5, 16.5, 8.5, closed=False)
    elif name == "close":
        p.drawLine(QPointF(6, 6), QPointF(18, 18))
        p.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif name == "pin":
        poly(8, 3, 16, 3, 15, 5, 15, 10, 18, 14, 6, 14, 9, 10, 9, 5, filled=True)
        p.drawLine(QPointF(12, 14), QPointF(12, 21.5))
    # roles
    elif name == "broadcaster":  # video camera
        p.setBrush(fill)
        p.drawRoundedRect(QRectF(2.5, 7, 13, 10), 2, 2)
        poly(16.5, 12, 21.5, 8, 21.5, 16, filled=True)
    elif name == "moderator":  # shield
        path = QPainterPath(QPointF(12, 2.5))
        path.lineTo(20, 5.5)
        path.cubicTo(20, 13, 17, 18.5, 12, 21.5)
        path.cubicTo(7, 18.5, 4, 13, 4, 5.5)
        path.closeSubpath()
        p.setBrush(fill)
        p.drawPath(path)
    elif name in ("vip", "bits"):  # diamond
        poly(12, 3, 20, 10, 12, 21, 4, 10, filled=True)
    elif name in ("subscriber", "sub", "member"):  # star
        p.setBrush(fill)
        p.drawPolygon(_star(12, 12.5, 10, 4.2))
    elif name == "verified":  # check in a filled circle
        p.setBrush(fill)
        p.drawEllipse(QPointF(12, 12), 9.5, 9.5)
        p.setPen(QPen(QColor("#18181b"), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        poly(7.5, 12.5, 10.5, 15.5, 16.5, 8.5, closed=False)
    # alerts
    elif name == "gift":
        p.setBrush(fill)
        p.drawRect(QRectF(4, 10, 16, 11))
        p.drawRect(QRectF(3, 7, 18, 3))
        p.setPen(QPen(QColor("#18181b"), 2))
        p.drawLine(QPointF(12, 7), QPointF(12, 21))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(9.5, 4.8), 2.5, 1.8)
        p.drawEllipse(QPointF(14.5, 4.8), 2.5, 1.8)
    elif name == "raid":  # double chevron forward
        poly(4, 5, 11, 12, 4, 19, closed=False)
        poly(12, 5, 19, 12, 12, 19, closed=False)
    elif name == "superchat":  # coin
        p.setBrush(fill)
        p.drawEllipse(QPointF(12, 12), 9.5, 9.5)
        p.setPen(QPen(QColor("#18181b"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        path = QPainterPath(QPointF(15, 8.5))
        path.cubicTo(13, 7, 8.5, 7.5, 9, 10)
        path.cubicTo(9.5, 12.5, 15, 11.5, 15, 14.5)
        path.cubicTo(15, 17, 10.5, 17, 9, 15.5)
        p.drawPath(path)
        p.drawLine(QPointF(12, 5.5), QPointF(12, 7.5))
        p.drawLine(QPointF(12, 16.5), QPointF(12, 18.5))


def image(name: str, px: int, color: str | None = None) -> QImage:
    """Icon `name` as a px×px image (transparent background)."""
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(px / 24, px / 24)
    _draw(p, name, QColor(color or COLORS.get(name, DEFAULT)))
    p.end()
    return img


def icon(name: str, color: str | None = None) -> QIcon:
    """QIcon with sharp sizes for normal and HiDPI screens. Without a colour it follows the
    text colour of the desktop theme (light or dark), greyed out when disabled."""
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication

    pal = QApplication.palette()
    normal = color or pal.color(QPalette.ColorGroup.Active, QPalette.ColorRole.ButtonText).name()
    disabled = pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText).name()
    ic = QIcon()
    for px in (16, 24, 32, 48, 64):
        ic.addPixmap(QPixmap.fromImage(image(name, px, normal)), QIcon.Mode.Normal)
        ic.addPixmap(QPixmap.fromImage(image(name, px, disabled)), QIcon.Mode.Disabled)
    return ic


NAMES = ("user", "at", "trash", "clock", "ban", "check", "unban", "close", "pin", "broadcaster", "moderator",
         "vip", "bits", "subscriber", "sub", "member", "verified", "gift", "raid", "superchat")
