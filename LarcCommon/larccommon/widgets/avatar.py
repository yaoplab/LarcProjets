import zlib

from PySide6.QtGui import QPixmap, QColor, QPainter, QFont
from PySide6.QtCore import Qt


_COLORS = ['#e74c3c', '#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c']


def avatar_color_index(last_name: str, first_name: str) -> int:
    """Index dans _COLORS — CRC32 de l'UTF-8, stable entre lancements
    (hash() varie avec PYTHONHASHSEED)."""
    return zlib.crc32((last_name + first_name).encode("utf-8")) % len(_COLORS)


def make_avatar(last_name: str, first_name: str, size: int = 89, font_px: int = 34) -> QPixmap:
    initials = (last_name[:1] + first_name[:1]).upper() or '?'
    color = _COLORS[avatar_color_index(last_name, first_name)]
    px = QPixmap(size, size)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor('#fff'))
    font = p.font()
    font.setPixelSize(font_px)
    font.setBold(True)
    p.setFont(font)
    p.drawText(px.rect(), Qt.AlignCenter, initials)
    p.end()
    return px


def make_initials_avatar(name: str, size: int = 100) -> QPixmap:
    """Avatar à initiales (nom complet) avec couleur déterministe, stable entre
    sessions — carré arrondi, utilisé par les vignettes du personnel."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    hue = sum(ord(c) for c in (name or "?")) % 360
    p.setBrush(QColor.fromHsl(hue, 160, 120))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(0, 0, size, size, size // 4, size // 4)
    initials = "".join(part[0].upper() for part in (name or "?").split()[:2]) or "?"
    p.setPen(QColor(255, 255, 255))
    font = QFont("Segoe UI", size // 3, QFont.Bold)
    p.setFont(font)
    p.drawText(0, 0, size, size, Qt.AlignCenter, initials)
    p.end()
    return pix
