# SPDX-License-Identifier: GPL-3.0-or-later
"""Emote images: scaled once, animated GIFs play, bounded cache, size limit."""
import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from multichat.ui import chat_view
from multichat.ui.chat_view import EMOTE_H, ChatView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def png(w=112, h=112) -> QByteArray:
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(QColor("#f5c542"))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return data


def test_static_images_are_scaled_once(app):
    v = ChatView()
    img = v._decode("https://x.invalid/a.png", png(224, 112))
    assert img is not None and img.height() == round(EMOTE_H * v.devicePixelRatioF())
    assert img.width() == 2 * img.height()  # aspect ratio kept


# 8×8 GIF with two frames (red, blue)
GIF2 = bytes.fromhex(
    "47494638396108000800810000ff000000000000000000000021ff0b4e45545343415045322e30030100000021f904000a0000002c000000000800080000080f0001081c48b0a0c18308132a4c18100021f904010a0001002c0000000008000800810000ff000000000000000000080f0001081c48b0a0c18308132a4c1810003b"
)


def test_animated_gif_plays(app):
    v = ChatView()
    img = v._decode("https://x.invalid/a.gif", QByteArray(GIF2))
    assert img is not None and img.height() == round(EMOTE_H * v.devicePixelRatioF())
    movie, _buf = v.movies["https://x.invalid/a.gif"]  # plays via QMovie
    assert movie.frameCount() == 2


def test_cache_is_bounded(app, monkeypatch):
    monkeypatch.setattr(chat_view, "MAX_CACHED", 3)
    v = ChatView()
    for i in range(5):
        v._store(f"https://x.invalid/{i}.png", QImage(4, 4, QImage.Format.Format_ARGB32))
    assert list(v.cache) == [f"https://x.invalid/{i}.png" for i in (2, 3, 4)]


def test_only_https_is_loaded(app):
    assert chat_view._allowed_url(QUrl("https://cdn.example/a.png"))
    assert not chat_view._allowed_url(QUrl("http://192.168.1.1/a.png"))
    assert chat_view._allowed_url(QUrl("http://127.0.0.1:8000/a.png"))


def test_drawn_icons_are_not_empty():
    from PyQt6.QtWidgets import QApplication

    from multichat.ui import icons

    QApplication.instance() or QApplication([])
    for name in icons.NAMES:
        img = icons.image(name, 24)
        assert any(img.pixelColor(x, y).alpha() for x in range(24) for y in range(24)), name
    assert not icons.icon("user").isNull()
