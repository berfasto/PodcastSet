"""Ilustraciones vectoriales limpias de guía para ranuras de sprites y personajes provisionales."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap


def draw_face(p, key, r, color):
    """key: closed/mid/open/wide, half_*, blink_*, o idle_alt."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    eyes_closed = key.startswith("blink_")
    eyes_half = key.startswith("half_")
    idle_alt = key == "idle_alt"
    mouth = "closed" if idle_alt else key.replace("blink_", "").replace("half_", "")
    c = QColor(color)
    ink = QColor("#121216")
    w, cx, cy = r.width(), r.center().x(), r.center().y()
    p.setPen(QPen(c.darker(145), max(1.2, w * 0.025)))
    p.setBrush(c)
    p.drawEllipse(r)
    ex, ey = w * 0.19, cy - w * 0.11
    if eyes_closed:
        p.setPen(QPen(ink, max(1.4, w * 0.032), Qt.SolidLine, Qt.RoundCap))
        for s in (-1, 1):
            p.drawLine(QPointF(cx + s * ex - w * 0.07, ey), QPointF(cx + s * ex + w * 0.07, ey))
    elif eyes_half:
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        for s in (-1, 1):
            p.drawEllipse(QPointF(cx + s * ex, ey + w * 0.014), w * 0.052, w * 0.024)
        p.setPen(QPen(ink, max(1.3, w * 0.028), Qt.SolidLine, Qt.RoundCap))
        for s in (-1, 1):
            p.drawLine(QPointF(cx + s * ex - w * 0.072, ey - w * 0.015),
                       QPointF(cx + s * ex + w * 0.072, ey - w * 0.015))
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        for s in (-1, 1):
            p.drawEllipse(QPointF(cx + s * ex, ey), w * 0.048, w * 0.058)
    my = cy + w * 0.19
    if idle_alt:
        p.setPen(QPen(ink, max(1.4, w * 0.032), Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(cx - w * 0.1, my - w * 0.05, w * 0.2, w * 0.10), 200 * 16, 140 * 16)
    elif mouth == "closed":
        p.setPen(QPen(ink, max(1.4, w * 0.032), Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - w * 0.10, my), QPointF(cx + w * 0.10, my))
    else:
        rw, rh = {"mid": (0.075, 0.032), "open": (0.095, 0.072), "wide": (0.125, 0.125)}.get(mouth, (0.075, 0.032))
        p.setPen(Qt.NoPen)
        p.setBrush(ink)
        p.drawEllipse(QPointF(cx, my + rh * w * 0.28), rw * w, rh * w)
    p.restore()


_GUIDE_CACHE = {}


def guide_pixmap(key, size=96, color="#6B7280"):
    ck = (key, size, color)
    if ck in _GUIDE_CACHE:
        return _GUIDE_CACHE[ck]
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    m = size * 0.08
    draw_face(p, key, QRectF(m, m, size - 2 * m, size - 2 * m), color)
    p.end()
    if len(_GUIDE_CACHE) > 256:
        _GUIDE_CACHE.clear()
    _GUIDE_CACHE[ck] = pm
    return pm


def placeholder_character_image(name, key, color):
    """Personaje provisional (400x560) como QImage seguro para hilos."""
    img = QImage(400, 560, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    p.setPen(QPen(c.darker(150), 4))
    p.setBrush(c.darker(130))
    p.drawRoundedRect(QRectF(72, 336, 256, 220), 56, 56)
    draw_face(p, key, QRectF(42, 22, 316, 316), color)
    p.setPen(QColor("#F4F4F6"))
    p.setFont(QFont("Segoe UI", 20, QFont.Bold))
    p.drawText(QRectF(76, 430, 248, 60), Qt.AlignCenter, name[:16])
    p.end()
    return img


def placeholder_character(name, key, color):
    return QPixmap.fromImage(placeholder_character_image(name, key, color))
