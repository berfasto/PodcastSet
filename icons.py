"""Sistema de iconos vectoriales procedimentales Studio UI (rejilla 24x24, supersampling 3x):
- Trazo 1.85 px (RoundCap, RoundJoin).
- Relleno suave al 22% de opacidad en cuerpos principales.
- Relleno sólido selectivo en detalles focales y vaciados CompositionMode_Clear.
- 4 estados automáticos según el tema activo: Reposo (dim), Hover (text), Activo/Checked (selection_text), Deshabilitado (muted 43%)."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from theme import C

_PIX_CACHE = {}
_STATE_ICON_CACHE = {}


def clear_cache():
    _PIX_CACHE.clear()
    _STATE_ICON_CACHE.clear()


def _soft_color(col, factor=0.22):
    sc = QColor(col)
    sc.setAlpha(max(0, min(255, int(round(sc.alpha() * factor)))))
    return sc


def _draw_procedural(p: QPainter, name: str, col: QColor):
    pen = QPen(col, 1.85, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    soft = _soft_color(col, 0.22)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)

    def clear_circle(cx, cy, r):
        p.save()
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.setPen(Qt.NoPen)
        p.setBrush(Qt.black)
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.restore()
        p.setPen(pen)

    def clear_rect(x, y, w, h, rx=0.0):
        p.save()
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.setPen(Qt.NoPen)
        p.setBrush(Qt.black)
        if rx > 0:
            p.drawRoundedRect(QRectF(x, y, w, h), rx, rx)
        else:
            p.drawRect(QRectF(x, y, w, h))
        p.restore()
        p.setPen(pen)

    # Normalizar alias
    alias = {
        "x": "close",
        "eyedropper": "pipette",
        "bone": "rig",
        "eq": "sliders",
        "broadcast": "live",
        "light": "sun",
    }
    k = alias.get(name, name)

    if k == "app_logo":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3, 3, 18, 18), 5.0, 5.0)
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(9.3, 10.5), 1.35, 1.6)
        p.drawEllipse(QPointF(14.7, 10.5), 1.35, 1.6)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(8.5, 14.5)
        path.quadTo(12.0, 17.5, 15.5, 14.5)
        p.drawPath(path)

    elif k == "home":
        path = QPainterPath()
        path.moveTo(3.5, 10.2)
        path.lineTo(12.0, 3.2)
        path.lineTo(20.5, 10.2)
        path.lineTo(20.5, 19.5)
        path.quadTo(20.5, 20.5, 19.5, 20.5)
        path.lineTo(4.5, 20.5)
        path.quadTo(3.5, 20.5, 3.5, 19.5)
        path.closeSubpath()
        p.setBrush(soft)
        p.drawPath(path)
        clear_rect(9.6, 13.2, 4.8, 7.3, 1.0)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(9.6, 13.2, 4.8, 7.3), 1.0, 1.0)

    elif k == "plus":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawLine(QPointF(5, 12), QPointF(19, 12))

    elif k == "minus":
        p.drawLine(QPointF(5, 12), QPointF(19, 12))

    elif k == "close":
        p.drawLine(QPointF(6.2, 6.2), QPointF(17.8, 17.8))
        p.drawLine(QPointF(17.8, 6.2), QPointF(6.2, 17.8))

    elif k == "check":
        p.drawLine(QPointF(4.5, 12.5), QPointF(9.5, 17.5))
        p.drawLine(QPointF(9.5, 17.5), QPointF(19.5, 6.5))

    elif k == "file_new":
        path = QPainterPath()
        path.moveTo(6, 3)
        path.lineTo(14, 3)
        path.lineTo(19, 8)
        path.lineTo(19, 20)
        path.quadTo(19, 21, 18, 21)
        path.lineTo(6, 21)
        path.quadTo(5, 21, 5, 20)
        path.lineTo(5, 4)
        path.quadTo(5, 3, 6, 3)
        p.setBrush(soft)
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(14, 3), QPointF(14, 8))
        p.drawLine(QPointF(14, 8), QPointF(19, 8))
        p.drawLine(QPointF(12, 11.5), QPointF(12, 17.5))
        p.drawLine(QPointF(9, 14.5), QPointF(15, 14.5))

    elif k in ("folder", "folder_open", "folder_add"):
        path = QPainterPath()
        path.moveTo(3, 6.5)
        path.quadTo(3, 5, 4.5, 5)
        path.lineTo(9.2, 5)
        path.lineTo(11.2, 7.5)
        path.lineTo(19.5, 7.5)
        path.quadTo(21, 7.5, 21, 9)
        path.lineTo(21, 18.5)
        path.quadTo(21, 20, 19.5, 20)
        path.lineTo(4.5, 20)
        path.quadTo(3, 20, 3, 18.5)
        path.closeSubpath()
        p.setBrush(soft)
        p.drawPath(path)
        p.setBrush(Qt.NoBrush)
        if k == "folder_open":
            p.drawLine(QPointF(3.5, 11), QPointF(20.5, 11))
        elif k == "folder_add":
            p.drawLine(QPointF(12, 11), QPointF(12, 16.5))
            p.drawLine(QPointF(9.25, 13.75), QPointF(14.75, 13.75))

    elif k == "save":
        path = QPainterPath()
        path.moveTo(5, 3.5)
        path.lineTo(16.2, 3.5)
        path.lineTo(20.5, 7.8)
        path.lineTo(20.5, 19.5)
        path.quadTo(20.5, 20.5, 19.5, 20.5)
        path.lineTo(4.5, 20.5)
        path.quadTo(3.5, 20.5, 3.5, 19.5)
        path.lineTo(3.5, 5)
        path.quadTo(3.5, 3.5, 5, 3.5)
        p.setBrush(soft)
        p.drawPath(path)
        clear_rect(7.5, 13.2, 9.0, 7.3, 1.0)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(7.5, 13.2, 9.0, 7.3), 1.0, 1.0)
        p.drawRect(QRectF(7.5, 3.5, 7.2, 4.5))

    elif k == "undo":
        p.drawLine(QPointF(4, 9), QPointF(8.5, 4.5))
        p.drawLine(QPointF(4, 9), QPointF(8.5, 13.5))
        path = QPainterPath()
        path.moveTo(4.2, 9)
        path.lineTo(14.5, 9)
        path.arcTo(QRectF(9.5, 9, 10, 10), 90, -180)
        path.lineTo(10.5, 19)
        p.drawPath(path)

    elif k == "redo":
        p.drawLine(QPointF(20, 9), QPointF(15.5, 4.5))
        p.drawLine(QPointF(20, 9), QPointF(15.5, 13.5))
        path = QPainterPath()
        path.moveTo(19.8, 9)
        path.lineTo(9.5, 9)
        path.arcTo(QRectF(4.5, 9, 10, 10), 90, 180)
        path.lineTo(13.5, 19)
        p.drawPath(path)

    elif k == "edit":
        poly = QPolygonF([QPointF(15.8, 3.8), QPointF(20.2, 8.2), QPointF(8.2, 20.2), QPointF(3.5, 20.5), QPointF(3.8, 15.8)])
        p.setBrush(soft)
        p.drawPolygon(poly)
        p.drawLine(QPointF(13.5, 6.1), QPointF(17.9, 10.5))

    elif k == "trash":
        p.drawLine(QPointF(4, 6.5), QPointF(20, 6.5))
        p.drawRoundedRect(QRectF(9, 3.5, 6, 3), 1.0, 1.0)
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(6.2, 6.5, 11.6, 14), 2.0, 2.0)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(10, 10.5), QPointF(10, 16.8))
        p.drawLine(QPointF(14, 10.5), QPointF(14, 16.8))

    elif k in ("camera", "webcam"):
        if k == "webcam":
            p.setBrush(soft)
            p.drawEllipse(QPointF(12, 10), 6.5, 6.5)
            clear_circle(12, 10, 2.4)
            p.setBrush(col)
            p.drawEllipse(QPointF(12, 10), 2.1, 2.1)
            p.setBrush(Qt.NoBrush)
            p.drawLine(QPointF(12, 16.5), QPointF(12, 20.2))
            p.drawLine(QPointF(7.5, 20.2), QPointF(16.5, 20.2))
        else:
            path = QPainterPath()
            path.moveTo(3.5, 8.5)
            path.lineTo(6.8, 8.5)
            path.lineTo(8.6, 5.5)
            path.lineTo(15.4, 5.5)
            path.lineTo(17.2, 8.5)
            path.lineTo(20.5, 8.5)
            path.lineTo(20.5, 19.2)
            path.lineTo(3.5, 19.2)
            path.closeSubpath()
            p.setBrush(soft)
            p.drawPath(path)
            clear_circle(12, 13.5, 3.2)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(12, 13.5), 3.2, 3.2)

    elif k in ("face_track", "calibrate", "center"):
        p.drawLine(QPointF(4, 8), QPointF(4, 4.5)); p.drawLine(QPointF(4, 4.5), QPointF(7.5, 4.5))
        p.drawLine(QPointF(20, 8), QPointF(20, 4.5)); p.drawLine(QPointF(20, 4.5), QPointF(16.5, 4.5))
        p.drawLine(QPointF(4, 16), QPointF(4, 19.5)); p.drawLine(QPointF(4, 19.5), QPointF(7.5, 19.5))
        p.drawLine(QPointF(20, 16), QPointF(20, 19.5)); p.drawLine(QPointF(20, 19.5), QPointF(16.5, 19.5))
        p.setBrush(soft)
        p.drawEllipse(QPointF(12, 12), 4.2, 4.2)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(12, 12), 1.5, 1.5)

    elif k in ("mic", "mic_off"):
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(9, 3, 6, 10.5), 3.0, 3.0)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(5.5, 10.5)
        path.arcTo(QRectF(5.5, 4.5, 13, 12.5), 180, 180)
        p.drawPath(path)
        p.drawLine(QPointF(12, 17), QPointF(12, 21))
        p.drawLine(QPointF(8.5, 21), QPointF(15.5, 21))
        if k == "mic_off":
            p.drawLine(QPointF(4, 4), QPointF(20, 20))

    elif k in ("volume", "volume_off"):
        poly = QPolygonF([QPointF(3.5, 9.5), QPointF(7.5, 9.5), QPointF(12, 5.5), QPointF(12, 18.5), QPointF(7.5, 14.5), QPointF(3.5, 14.5)])
        p.setBrush(soft)
        p.drawPolygon(poly)
        p.setBrush(Qt.NoBrush)
        if k == "volume":
            path = QPainterPath()
            path.arcMoveTo(QRectF(11.5, 8, 5.5, 8), -45)
            path.arcTo(QRectF(11.5, 8, 5.5, 8), -45, 90)
            p.drawPath(path)
            path2 = QPainterPath()
            path2.arcMoveTo(QRectF(11.5, 5, 9, 14), -45)
            path2.arcTo(QRectF(11.5, 5, 9, 14), -45, 90)
            p.drawPath(path2)
        else:
            p.drawLine(QPointF(15.5, 9.5), QPointF(20.5, 14.5))
            p.drawLine(QPointF(20.5, 9.5), QPointF(15.5, 14.5))

    elif k in ("wave", "voice"):
        for x, h in ((4, 5), (8, 11), (12, 16), (16, 10), (20, 6)):
            p.drawLine(QPointF(x, 12 - h * 0.5), QPointF(x, 12 + h * 0.5))

    elif k == "sliders":
        for x, cy in ((5.5, 14.5), (12.0, 9.0), (18.5, 15.5)):
            p.drawLine(QPointF(x, 4), QPointF(x, 20))
            clear_circle(x, cy, 2.4)
            p.setBrush(soft)
            p.drawEllipse(QPointF(x, cy), 2.2, 2.2)
            p.setBrush(Qt.NoBrush)

    elif k in ("play", "live"):
        poly = QPolygonF([QPointF(7.2, 4.8), QPointF(19.2, 12.0), QPointF(7.2, 19.2)])
        p.setBrush(soft)
        p.drawPolygon(poly)

    elif k == "pause":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(6.2, 5, 4.0, 14), 1.2, 1.2)
        p.drawRoundedRect(QRectF(13.8, 5, 4.0, 14), 1.2, 1.2)

    elif k == "stop":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(6, 6, 12, 12), 2.0, 2.0)

    elif k == "record":
        p.setBrush(soft)
        p.drawEllipse(QPointF(12, 12), 7.5, 7.5)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(12, 12), 4.2, 4.2)

    elif k == "skip_back":
        p.drawLine(QPointF(6, 5.5), QPointF(6, 18.5))
        poly = QPolygonF([QPointF(18.5, 5.8), QPointF(9.5, 12.0), QPointF(18.5, 18.2)])
        p.setBrush(soft)
        p.drawPolygon(poly)

    elif k == "loop":
        p.drawLine(QPointF(16.5, 3.5), QPointF(19.5, 6.5))
        p.drawLine(QPointF(19.5, 6.5), QPointF(16.5, 9.5))
        p.drawLine(QPointF(7.5, 14.5), QPointF(4.5, 17.5))
        p.drawLine(QPointF(4.5, 17.5), QPointF(7.5, 20.5))
        p.drawLine(QPointF(5, 10), QPointF(5, 8.5))
        p.drawLine(QPointF(5, 6.5), QPointF(19, 6.5))
        p.drawLine(QPointF(19, 14), QPointF(19, 17.5))
        p.drawLine(QPointF(19, 17.5), QPointF(5, 17.5))

    elif k in ("zoom_in", "zoom_out", "search"):
        p.setBrush(soft)
        p.drawEllipse(QPointF(10.8, 10.8), 6.5, 6.5)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(15.6, 15.6), QPointF(20.2, 20.2))
        if k == "zoom_in":
            p.drawLine(QPointF(10.8, 8.0), QPointF(10.8, 13.6))
            p.drawLine(QPointF(8.0, 10.8), QPointF(13.6, 10.8))
        elif k == "zoom_out":
            p.drawLine(QPointF(8.0, 10.8), QPointF(13.6, 10.8))

    elif k == "fit":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(7, 7, 10, 10), 2.0, 2.0)
        p.setBrush(Qt.NoBrush)
        for sx, sy in ((3.5, 3.5), (20.5, 3.5), (3.5, 20.5), (20.5, 20.5)):
            dx = 3.5 if sx < 12 else -3.5
            dy = 3.5 if sy < 12 else -3.5
            p.drawLine(QPointF(sx, sy), QPointF(sx + dx, sy))
            p.drawLine(QPointF(sx, sy), QPointF(sx, sy + dy))

    elif k in ("moon", "moon_fill"):
        path = QPainterPath()
        path.addEllipse(QPointF(12, 12), 7.8, 7.8)
        cut = QPainterPath()
        cut.addEllipse(QPointF(15.4, 9.2), 6.5, 6.5)
        crescent = path.subtracted(cut)
        p.setBrush(col if k == "moon_fill" else soft)
        p.drawPath(crescent)

    elif k == "sun":
        p.setBrush(soft)
        p.drawEllipse(QPointF(12, 12), 4.5, 4.5)
        for i in range(8):
            a = i * math.pi / 4.0
            p.drawLine(QPointF(12 + math.cos(a) * 6.8, 12 + math.sin(a) * 6.8),
                       QPointF(12 + math.cos(a) * 9.2, 12 + math.sin(a) * 9.2))

    elif k == "settings":
        p.setBrush(soft)
        p.drawEllipse(QPointF(12, 12), 6.8, 6.8)
        for i in range(8):
            a = i * math.pi / 4.0
            p.drawLine(QPointF(12 + math.cos(a) * 6.8, 12 + math.sin(a) * 6.8),
                       QPointF(12 + math.cos(a) * 9.2, 12 + math.sin(a) * 9.2))
        clear_circle(12, 12, 2.8)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(12, 12), 2.8, 2.8)

    elif k == "pipette":
        p.setBrush(soft)
        poly = QPolygonF([QPointF(14.5, 5.5), QPointF(18.5, 9.5), QPointF(9.0, 19.0), QPointF(4.5, 19.5), QPointF(5.0, 15.0)])
        p.drawPolygon(poly)
        p.setBrush(col)
        p.drawEllipse(QPointF(17.5, 6.5), 2.2, 2.2)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(13.0, 7.0), QPointF(17.0, 11.0))

    elif k in ("palette", "swatches"):
        if k == "swatches":
            p.setBrush(soft)
            p.drawRoundedRect(QRectF(3.5, 3.5, 7.2, 7.2), 1.8, 1.8)
            p.drawRoundedRect(QRectF(13.3, 3.5, 7.2, 7.2), 1.8, 1.8)
            p.drawRoundedRect(QRectF(3.5, 13.3, 7.2, 7.2), 1.8, 1.8)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(13.3, 13.3, 7.2, 7.2), 1.8, 1.8)
        else:
            p.setBrush(soft)
            p.drawEllipse(QPointF(12, 12), 8.8, 8.8)
            clear_circle(15.5, 15.2, 2.1)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            for cx, cy in ((8.5, 10.5), (10.5, 7.5), (14.5, 8.2), (8.8, 14.6)):
                p.drawEllipse(QPointF(cx, cy), 1.25, 1.25)

    elif k == "copy":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(8.5, 8.5, 11.5, 11.5), 2.0, 2.0)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(15.5, 8.5)
        path.lineTo(15.5, 5.5)
        path.quadTo(15.5, 4, 14, 4)
        path.lineTo(5.5, 4)
        path.quadTo(4, 4, 4, 5.5)
        path.lineTo(4, 14)
        path.quadTo(4, 15.5, 5.5, 15.5)
        path.lineTo(8.5, 15.5)
        p.drawPath(path)

    elif k == "cursor":
        poly = QPolygonF([QPointF(5.5, 4.0), QPointF(5.5, 18.8), QPointF(9.8, 14.8), QPointF(13.2, 20.2), QPointF(15.4, 19.0), QPointF(12.0, 13.6), QPointF(17.5, 13.6)])
        p.setBrush(soft)
        p.drawPolygon(poly)

    elif k == "move":
        p.drawLine(QPointF(12, 3), QPointF(12, 21))
        p.drawLine(QPointF(3, 12), QPointF(21, 12))
        for ax, ay, bx, by, cx, cy in (
            (9.5, 5.5, 12, 3, 14.5, 5.5),
            (9.5, 18.5, 12, 21, 14.5, 18.5),
            (5.5, 9.5, 3, 12, 5.5, 14.5),
            (18.5, 9.5, 21, 12, 18.5, 14.5),
        ):
            p.drawLine(QPointF(ax, ay), QPointF(bx, by))
            p.drawLine(QPointF(bx, by), QPointF(cx, cy))

    elif k == "hand":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(6.5, 9.5, 11.5, 10.5), 4.5, 4.5)
        p.setBrush(Qt.NoBrush)
        for x, y0 in ((8.0, 5.5), (10.8, 4.0), (13.6, 4.5), (16.4, 6.2)):
            p.drawLine(QPointF(x, y0), QPointF(x, 11.0))

    elif k == "scissors":
        p.setBrush(soft)
        p.drawEllipse(QPointF(6.5, 6.8), 2.8, 2.8)
        p.drawEllipse(QPointF(6.5, 17.2), 2.8, 2.8)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(8.8, 8.6), QPointF(19.5, 18.8))
        p.drawLine(QPointF(8.8, 15.4), QPointF(19.5, 5.2))

    elif k == "split":
        p.drawLine(QPointF(12, 3.5), QPointF(12, 20.5))
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3.5, 7.5, 5.2, 9.0), 1.4, 1.4)
        p.drawRoundedRect(QRectF(15.3, 7.5, 5.2, 9.0), 1.4, 1.4)

    elif k == "grid":
        p.setBrush(soft)
        for rx, ry in ((3.5, 3.5), (13.3, 3.5), (3.5, 13.3), (13.3, 13.3)):
            p.drawRoundedRect(QRectF(rx, ry, 7.2, 7.2), 1.5, 1.5)

    elif k == "magnet":
        path = QPainterPath()
        path.moveTo(6, 5)
        path.lineTo(6, 12.5)
        path.arcTo(QRectF(6, 6.5, 12, 12), 180, 180)
        path.lineTo(18, 5)
        p.drawPath(path)
        p.drawLine(QPointF(4.5, 8.5), QPointF(7.5, 8.5))
        p.drawLine(QPointF(16.5, 8.5), QPointF(19.5, 8.5))

    elif k in ("eye", "eye_off"):
        path = QPainterPath()
        path.moveTo(2.5, 12)
        path.quadTo(12, 4.2, 21.5, 12)
        path.quadTo(12, 19.8, 2.5, 12)
        p.setBrush(soft)
        p.drawPath(path)
        clear_circle(12, 12, 2.7)
        p.setBrush(col)
        p.drawEllipse(QPointF(12, 12), 2.2, 2.2)
        p.setBrush(Qt.NoBrush)
        if k == "eye_off":
            p.drawLine(QPointF(4, 4), QPointF(20, 20))

    elif k in ("lock", "unlock"):
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(5, 11, 14, 9.5), 2.2, 2.2)
        clear_circle(12, 15.6, 1.4)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(8, 11)
        path.lineTo(8, 7.8)
        path.arcTo(QRectF(8, 3.8, 8, 8), 180, -180 if k == "lock" else -130)
        if k == "lock":
            path.lineTo(16, 11)
        p.drawPath(path)

    elif k == "layers":
        top = QPolygonF([QPointF(12, 3.5), QPointF(21, 8.2), QPointF(12, 12.8), QPointF(3, 8.2)])
        p.setBrush(soft)
        p.drawPolygon(top)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(3, 12.8), QPointF(12, 17.2), QPointF(21, 12.8)]))
        p.drawPolyline(QPolygonF([QPointF(3, 16.8), QPointF(12, 21.0), QPointF(21, 16.8)]))

    elif k in ("rig", "tree"):
        p.setBrush(soft)
        p.drawEllipse(QPointF(12, 5.2), 2.8, 2.8)
        p.drawEllipse(QPointF(6.2, 18.2), 2.3, 2.3)
        p.drawEllipse(QPointF(17.8, 18.2), 2.3, 2.3)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(12, 8.0), QPointF(12, 13.0))
        p.drawLine(QPointF(12, 13.0), QPointF(7.2, 16.3))
        p.drawLine(QPointF(12, 13.0), QPointF(16.8, 16.3))

    elif k == "cube":
        poly = QPolygonF([
            QPointF(12, 2.8), QPointF(20.2, 7.5), QPointF(20.2, 16.5),
            QPointF(12, 21.2), QPointF(3.8, 16.5), QPointF(3.8, 7.5),
        ])
        p.setBrush(soft)
        p.drawPolygon(poly)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(3.8, 7.5), QPointF(12, 12.2))
        p.drawLine(QPointF(20.2, 7.5), QPointF(12, 12.2))
        p.drawLine(QPointF(12, 12.2), QPointF(12, 21.2))

    elif k in ("tv", "monitor"):
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3, 4, 18, 13), 2.2, 2.2)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(12, 17), QPointF(12, 20.2))
        p.drawLine(QPointF(8, 20.2), QPointF(16, 20.2))

    elif k == "smartphone":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(6.5, 2.8, 11, 18.4), 2.4, 2.4)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(10.5, 18.0), QPointF(13.5, 18.0))

    elif k == "user":
        p.setBrush(soft)
        p.drawEllipse(QPointF(12, 8), 3.8, 3.8)
        path = QPainterPath()
        path.moveTo(4.8, 20.2)
        path.arcTo(QRectF(4.8, 13.8, 14.4, 12.0), 180, -180)
        p.drawPath(path)

    elif k in ("emote", "smile"):
        p.setBrush(soft)
        p.drawEllipse(QPointF(12, 12), 8.8, 8.8)
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawEllipse(QPointF(9.0, 10.0), 1.2, 1.2)
        p.drawEllipse(QPointF(15.0, 10.0), 1.2, 1.2)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(8.5, 14.2)
        path.quadTo(12.0, 17.5, 15.5, 14.2)
        p.drawPath(path)

    elif k in ("sparkle", "fx"):
        poly = QPolygonF([
            QPointF(12, 3), QPointF(14.1, 9.9), QPointF(21, 12),
            QPointF(14.1, 14.1), QPointF(12, 21), QPointF(9.9, 14.1),
            QPointF(3, 12), QPointF(9.9, 9.9),
        ])
        p.setBrush(soft)
        p.drawPolygon(poly)

    elif k == "music":
        p.drawLine(QPointF(9, 17.5), QPointF(9, 5.5))
        p.drawLine(QPointF(9, 5.5), QPointF(19.5, 3.8))
        p.drawLine(QPointF(19.5, 3.8), QPointF(19.5, 15.8))
        p.setBrush(soft)
        p.drawEllipse(QPointF(6.5, 17.5), 2.6, 2.6)
        p.drawEllipse(QPointF(17.0, 15.8), 2.6, 2.6)

    elif k == "timeline":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3.5, 4.5, 12, 3.6), 1.5, 1.5)
        p.drawRoundedRect(QRectF(8.5, 10.2, 12, 3.6), 1.5, 1.5)
        p.drawRoundedRect(QRectF(4.5, 15.9, 9.5, 3.6), 1.5, 1.5)

    elif k == "image":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3.5, 3.5, 17, 17), 2.4, 2.4)
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(8.5, 8.8), 1.6, 1.6)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPolyline(QPolygonF([QPointF(4.2, 18.5), QPointF(10.5, 12.8), QPointF(14.5, 16.2), QPointF(17.2, 13.8), QPointF(20.2, 17.5)]))

    elif k == "text":
        p.drawLine(QPointF(4.5, 5), QPointF(19.5, 5))
        p.drawLine(QPointF(4.5, 5), QPointF(4.5, 7.2))
        p.drawLine(QPointF(19.5, 5), QPointF(19.5, 7.2))
        p.drawLine(QPointF(12, 5), QPointF(12, 19.5))
        p.drawLine(QPointF(9, 19.5), QPointF(15, 19.5))

    elif k == "export":
        p.drawLine(QPointF(12, 15), QPointF(12, 4))
        p.drawPolyline(QPolygonF([QPointF(7.8, 8.2), QPointF(12, 4), QPointF(16.2, 8.2)]))
        path = QPainterPath()
        path.moveTo(4, 14.5)
        path.lineTo(4, 19)
        path.quadTo(4, 20, 5, 20)
        path.lineTo(19, 20)
        path.quadTo(20, 20, 20, 19)
        path.lineTo(20, 14.5)
        p.drawPath(path)

    elif k == "download":
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        p.drawPolyline(QPolygonF([QPointF(7.8, 10.8), QPointF(12, 15), QPointF(16.2, 10.8)]))
        p.drawLine(QPointF(4.5, 19.8), QPointF(19.5, 19.8))

    elif k == "send":
        poly = QPolygonF([QPointF(20.5, 3.5), QPointF(14.2, 20.5), QPointF(10.8, 13.2), QPointF(3.5, 9.8)])
        p.setBrush(soft)
        p.drawPolygon(poly)
        p.drawLine(QPointF(20.5, 3.5), QPointF(10.8, 13.2))

    elif k == "refresh":
        path = QPainterPath()
        path.arcMoveTo(QRectF(4.2, 4.2, 15.6, 15.6), 35)
        path.arcTo(QRectF(4.2, 4.2, 15.6, 15.6), 35, 295)
        p.drawPath(path)
        p.drawPolyline(QPolygonF([QPointF(15.5, 4.2), QPointF(19.8, 4.2), QPointF(19.8, 8.5)]))

    elif k == "float":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(8.5, 4.0, 11.5, 11.5), 2.0, 2.0)
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(15, 18.5)
        path.lineTo(5.5, 18.5)
        path.quadTo(4, 18.5, 4, 17)
        path.lineTo(4, 9)
        p.drawPath(path)
        p.drawLine(QPointF(11.5, 12.5), QPointF(17.2, 6.8))
        p.drawPolyline(QPolygonF([QPointF(13.2, 6.8), QPointF(17.2, 6.8), QPointF(17.2, 10.8)]))

    elif k == "dock":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3.5, 4.0, 17.0, 16.0), 2.2, 2.2)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(14.2, 4.0), QPointF(14.2, 20.0))

    elif k in ("panel_left", "panel_right", "panel_bottom"):
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3.5, 3.5, 17, 17), 2.2, 2.2)
        p.setBrush(Qt.NoBrush)
        if k == "panel_left":
            p.drawLine(QPointF(9.2, 3.5), QPointF(9.2, 20.5))
        elif k == "panel_right":
            p.drawLine(QPointF(14.8, 3.5), QPointF(14.8, 20.5))
        else:
            p.drawLine(QPointF(3.5, 14.8), QPointF(20.5, 14.8))

    elif k == "chevron_up":
        p.drawPolyline(QPolygonF([QPointF(6, 15), QPointF(12, 9), QPointF(18, 15)]))
    elif k == "chevron_down":
        p.drawPolyline(QPolygonF([QPointF(6, 9), QPointF(12, 15), QPointF(18, 9)]))
    elif k == "chevron_left":
        p.drawPolyline(QPolygonF([QPointF(15, 6), QPointF(9, 12), QPointF(15, 18)]))
    elif k == "chevron_right":
        p.drawPolyline(QPolygonF([QPointF(9, 6), QPointF(15, 12), QPointF(9, 18)]))
    elif k == "arrow_up":
        p.drawLine(QPointF(12, 19), QPointF(12, 5))
        p.drawPolyline(QPolygonF([QPointF(7, 10), QPointF(12, 5), QPointF(17, 10)]))
    elif k == "arrow_down":
        p.drawLine(QPointF(12, 5), QPointF(12, 19))
        p.drawPolyline(QPolygonF([QPointF(7, 14), QPointF(12, 19), QPointF(17, 14)]))

    elif k == "chapter":
        p.drawLine(QPointF(5, 4), QPointF(5, 20))
        poly = QPolygonF([QPointF(5, 5), QPointF(18.5, 5), QPointF(15.8, 9.5), QPointF(18.5, 14), QPointF(5, 14)])
        p.setBrush(soft)
        p.drawPolygon(poly)

    elif k == "in_pt":
        p.drawPolyline(QPolygonF([QPointF(15, 4.5), QPointF(9, 4.5), QPointF(9, 19.5), QPointF(15, 19.5)]))
    elif k == "out_pt":
        p.drawPolyline(QPolygonF([QPointF(9, 4.5), QPointF(15, 4.5), QPointF(15, 19.5), QPointF(9, 19.5)]))

    elif k == "alpha":
        p.drawRoundedRect(QRectF(3.5, 3.5, 17, 17), 2.2, 2.2)
        p.setPen(Qt.NoPen)
        p.setBrush(_soft_color(col, 0.42))
        for rx, ry in ((4.5, 4.5), (14.5, 4.5), (9.5, 9.5), (4.5, 14.5), (14.5, 14.5)):
            p.drawRect(QRectF(rx, ry, 5, 5))

    elif k == "gpu":
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3.5, 6.5, 17, 11), 2.0, 2.0)
        clear_circle(9.2, 12.0, 2.5)
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(9.2, 12.0), 2.5, 2.5)
        p.drawLine(QPointF(14.5, 10.2), QPointF(17.8, 10.2))
        p.drawLine(QPointF(14.5, 13.8), QPointF(17.8, 13.8))

    elif k in ("merge", "group"):
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3.5, 4.0, 10.5, 10.5), 2.0, 2.0)
        p.drawRoundedRect(QRectF(10.0, 9.5, 10.5, 10.5), 2.0, 2.0)

    elif k == "accessories":
        p.setBrush(soft)
        p.drawEllipse(QPointF(7.2, 14.2), 3.6, 3.6)
        p.drawEllipse(QPointF(16.8, 14.2), 3.6, 3.6)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(10.8, 14.0), QPointF(13.2, 14.0))
        p.drawLine(QPointF(3.6, 14.0), QPointF(5.2, 7.8))
        p.drawLine(QPointF(20.4, 14.0), QPointF(18.8, 7.8))

    elif k == "bolt":
        poly = QPolygonF([QPointF(13.2, 3.0), QPointF(5.2, 13.5), QPointF(11.5, 13.5), QPointF(10.5, 21.0), QPointF(18.8, 10.5), QPointF(12.5, 10.5)])
        p.setBrush(soft)
        p.drawPolygon(poly)

    elif k == "flip_h":
        p.drawLine(QPointF(12, 3.5), QPointF(12, 20.5))
        p.setBrush(soft)
        p.drawPolygon(QPolygonF([QPointF(8.5, 7.5), QPointF(3.5, 12), QPointF(8.5, 16.5)]))
        p.drawPolygon(QPolygonF([QPointF(15.5, 7.5), QPointF(20.5, 12), QPointF(15.5, 16.5)]))

    elif k == "wind":
        p.drawLine(QPointF(3.5, 9), QPointF(17.5, 9))
        p.drawLine(QPointF(3.5, 13), QPointF(19.5, 13))
        p.drawLine(QPointF(3.5, 17), QPointF(14.5, 17))

    elif k == "pivot":
        p.setBrush(soft)
        p.drawEllipse(QPointF(12, 12), 4.0, 4.0)
        p.setBrush(Qt.NoBrush)
        p.drawLine(QPointF(12, 3), QPointF(12, 8))
        p.drawLine(QPointF(12, 16), QPointF(12, 21))
        p.drawLine(QPointF(3, 12), QPointF(8, 12))
        p.drawLine(QPointF(16, 12), QPointF(21, 12))

    elif k in ("film", "video"):
        p.setBrush(soft)
        p.drawRoundedRect(QRectF(3, 5.5, 13.5, 13), 2.2, 2.2)
        poly = QPolygonF([QPointF(16.5, 10), QPointF(21, 7.2), QPointF(21, 16.8), QPointF(16.5, 14)])
        p.drawPolygon(poly)

    else:  # "more"
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        for cx in (5.5, 12.0, 18.5):
            p.drawEllipse(QPointF(cx, 12.0), 1.65, 1.65)


def pixmap(name, color=None, size=16):
    """Renderiza el icono en rejilla 24x24 con supersampling 3x."""
    c_str = color if color is not None else C.get("dim", "#A1A9B8")
    qc = QColor(c_str) if not isinstance(c_str, QColor) else QColor(c_str)
    key = (name, qc.rgba(), int(size))
    if key in _PIX_CACHE:
        return _PIX_CACHE[key]

    ss = 3
    px_side = max(3, int(size) * ss)
    img = QImage(px_side, px_side, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
    scale = px_side / 24.0
    p.scale(scale, scale)
    _draw_procedural(p, name, qc)
    p.end()

    # Escalado suave a 2x HiDPI para nitidez cristalina en pantallas 100%..200%
    out_dpr = 2
    out_px = max(2, int(size) * out_dpr)
    scaled = img.scaled(out_px, out_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pm = QPixmap.fromImage(scaled)
    pm.setDevicePixelRatio(out_dpr)

    if len(_PIX_CACHE) > 1024:
        _PIX_CACHE.clear()
    _PIX_CACHE[key] = pm
    return pm


def state_icon(name, size=16):
    """Icono con los 4 estados automáticos del tema activo:
    Reposo (dim), Hover (text), Activo/Checked (selection_text), Deshabilitado (muted 43%)."""
    mode_sig = (C.get("mode"), C.get("dim"), C.get("text"), C.get("selection_text"), C.get("muted"))
    key = (name, int(size), mode_sig)
    if key in _STATE_ICON_CACHE:
        return _STATE_ICON_CACHE[key]

    ic = QIcon()
    pm_rest = pixmap(name, C["dim"], size)
    pm_hover = pixmap(name, C["text"], size)
    pm_active = pixmap(name, C["selection_text"], size)

    dis_col = QColor(C["muted"])
    dis_col.setAlpha(int(round(255 * 0.43)))
    pm_dis = pixmap(name, dis_col, size)

    ic.addPixmap(pm_rest, QIcon.Normal, QIcon.Off)
    ic.addPixmap(pm_hover, QIcon.Active, QIcon.Off)
    ic.addPixmap(pm_active, QIcon.Normal, QIcon.On)
    ic.addPixmap(pm_active, QIcon.Active, QIcon.On)
    ic.addPixmap(pm_active, QIcon.Selected, QIcon.Off)
    ic.addPixmap(pm_active, QIcon.Selected, QIcon.On)
    ic.addPixmap(pm_dis, QIcon.Disabled, QIcon.Off)
    ic.addPixmap(pm_dis, QIcon.Disabled, QIcon.On)

    if len(_STATE_ICON_CACHE) > 512:
        _STATE_ICON_CACHE.clear()
    _STATE_ICON_CACHE[key] = ic
    return ic


def icon(name, color=None, size=16):
    """Si color es None, devuelve state_icon dinámico de 4 estados; si se pasa color, tiñe con estado deshabilitado."""
    if color is None:
        return state_icon(name, size)
    ic = QIcon()
    pm = pixmap(name, color, size)
    dis_col = QColor(C["muted"])
    dis_col.setAlpha(int(round(255 * 0.43)))
    pm_dis = pixmap(name, dis_col, size)
    ic.addPixmap(pm, QIcon.Normal, QIcon.Off)
    ic.addPixmap(pm, QIcon.Active, QIcon.Off)
    ic.addPixmap(pm, QIcon.Normal, QIcon.On)
    ic.addPixmap(pm, QIcon.Active, QIcon.On)
    ic.addPixmap(pm_dis, QIcon.Disabled, QIcon.Off)
    ic.addPixmap(pm_dis, QIcon.Disabled, QIcon.On)
    return ic
