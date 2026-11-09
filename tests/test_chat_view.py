# SPDX-License-Identifier: GPL-3.0-or-later
"""Emote images: scaled once, animated GIFs play, bounded cache, size limit."""
import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QUrl
from PyQt6.QtGui import QColor, QImage, QTextCursor
from PyQt6.QtNetwork import QNetworkReply
from PyQt6.QtWidgets import QApplication

from multichat.ui import chat_view
from multichat.ui.chat_view import EMOTE_H, ChatView
from test_image_limits import ControlledReply


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


def test_evicted_images_leave_the_document(app, monkeypatch):
    """The image limit covers the document's copies too, not only the dict."""
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QImage, QTextDocument

    from multichat.ui import chat_view

    monkeypatch.setattr(chat_view, "MAX_CACHED", 3)
    v = chat_view.ChatView()
    T = QTextDocument.ResourceType.ImageResource.value
    big = QImage(400, 400, QImage.Format.Format_ARGB32)
    for i in range(5):
        v._store(f"https://example.invalid/{i}.png", big)
    assert len(v.cache) == 3
    first = v.document().resource(T, QUrl("https://example.invalid/0.png"))  # asks loadResource again
    assert first.size() == v.placeholder.size()
    assert v.document().resource(T, QUrl("https://example.invalid/4.png")).size() == big.size()



def test_name_links_find_their_message_without_unique_urls(app):
    """One text format per message (unique "msg:<uid>" links) made memory grow with every
    message – the links are all "msg:" now and the clicked line tells the message."""
    from PyQt6.QtGui import QTextCursor

    from multichat.chat.models import ChatMessage

    v = ChatView()
    v.resize(600, 400)
    v.document().setMaximumBlockCount(50)
    msgs = [ChatMessage("twitch", "c", f"user{i}", f"text {i}") for i in range(500)]
    for k in range(0, 500, 25):
        v.append_many(v.items(msgs[k:k + 25]))
    # text formats don't grow with the number of messages (images do – see COMPACT_AFTER)
    assert len([f for f in v.document().allFormats() if not f.isImageFormat()]) < 40
    v.show()
    app.processEvents()
    block = v.document().lastBlock()
    cur = QTextCursor(block)
    cur.setPosition(block.position() + block.text().index("user499") + 2)
    pos = v.cursorRect(cur).center()
    assert v.message_at(pos).toString() == f"msg:{msgs[-1].uid}"
    hits = []
    v.name_clicked.connect(lambda url: hits.append(url.toString()))
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from PyQt6.QtCore import QEvent

    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(kind, QPointF(pos), QPointF(v.viewport().mapToGlobal(pos)), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton if kind == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(v.viewport(), ev)
    assert hits == [f"msg:{msgs[-1].uid}"]


def test_view_is_compacted_after_many_lines(app, monkeypatch):
    """Qt keeps one format per inserted image until clear() – the view asks for a rebuild."""
    from multichat.chat.models import ChatMessage

    monkeypatch.setattr(chat_view, "COMPACT_AFTER", 100)
    v = ChatView()
    v.document().setMaximumBlockCount(50)
    msgs = [ChatMessage("twitch", "c", f"user{i}", f"text {i}") for i in range(150)]
    v.append_many(v.items(msgs[:100]))
    assert not v.needs_compaction
    v.append_many(v.items(msgs[100:]))
    grown = len(v.document().allFormats())
    assert v.needs_compaction and grown > 100
    v.rebuild(v.items(msgs[-50:]))
    assert not v.needs_compaction and len(v.document().allFormats()) < grown / 2


def test_compaction_preserves_paused_wrapped_line_and_message_ids(app, monkeypatch):
    monkeypatch.setattr(chat_view, "COMPACT_AFTER", 200)
    v = ChatView()
    v.resize(380, 250)
    items = [(i, f"<img src='mcicon:user'> user{i}: " + "wrapped text " * 12, None) for i in range(80)]
    v.append_many(items)
    v.show()
    app.processEvents()
    bar = v.verticalScrollBar()
    bar.setValue(bar.maximum() // 2 + 11)
    assert not v.follow
    anchor = v._read_anchor()
    text = v.toPlainText()
    for _ in range(140):
        v.update_messages([items[3]])
    assert v.needs_compaction
    grown = len(v.document().allFormats())
    v.compact()
    assert not v.needs_compaction and len(v.document().allFormats()) < grown
    assert v.toPlainText() == text and v._read_anchor()[:3] == anchor[:3]
    assert not v.follow
    block = v.document().firstBlock()
    ids = []
    while block.isValid():
        ids.append(block.userState())
        block = block.next()
    assert ids == list(range(80))
    v.close()


def test_rebuild_anchors_to_message_after_earlier_lines_are_trimmed(app):
    v = ChatView()
    v.resize(380, 250)
    items = [(i, f"user{i}: " + "wrapped text " * 8, None) for i in range(100)]
    v.append_many(items)
    v.show()
    app.processEvents()
    bar = v.verticalScrollBar()
    bar.setValue(bar.maximum() // 2 + 7)
    anchor = v._read_anchor()
    assert not v.follow and anchor[0] > 20
    v.rebuild(items[20:])
    assert v._read_anchor()[:3] == anchor[:3]
    v.close()


class ImageReply(ControlledReply):
    def __init__(self, data=None, parent=None):
        super().__init__(parent)
        if data is None:
            self.setError(QNetworkReply.NetworkError.ConnectionRefusedError, "controlled error")
        else:
            self.buffer.extend(bytes(data))


def test_transient_image_failure_expires_and_refreshes_existing_line(app, monkeypatch):
    from types import SimpleNamespace
    import time

    now = [time.monotonic()]
    monkeypatch.setattr(chat_view, "time", SimpleNamespace(monotonic=lambda: now[0]))
    v = ChatView()
    key = "https://cdn.invalid/emote.png"
    part = ("emote", key, "Kappa")
    requests = []

    def get(request):
        reply = ImageReply(png() if requests else None, v)
        requests.append(reply)
        return reply

    monkeypatch.setattr(v.nam, "get", get)
    v.image_failed.connect(lambda _key: v.update_messages([(7, v._part_html(part), None)]))
    v.append_many([(7, v._part_html(part), None)])
    v.show()
    app.processEvents()
    assert len(requests) == 1
    requests[0].finished.emit()
    app.processEvents()
    assert key in v.failed and v.toPlainText() == "Kappa"
    v.loadResource(chat_view.IMAGE_RES, QUrl(key))
    assert len(requests) == 1  # backoff prevents repeated immediate attempts
    now[0] += chat_view.IMAGE_RETRY_SECONDS + 1
    v._image_retry.stop()
    v._retry_images()
    app.processEvents()
    assert len(requests) == 2 and key not in v.failed
    requests[-1].finished.emit()
    assert key in v.cache
    assert key in v.toHtml() and "Kappa" not in v.toPlainText()
    v.close()


def test_negative_image_cache_and_each_retry_batch_are_bounded(app, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(chat_view, "MAX_FAILED", 5)
    monkeypatch.setattr(chat_view, "RETRY_BATCH", 2)
    now = [100.0]
    monkeypatch.setattr(chat_view, "time", SimpleNamespace(monotonic=lambda: now[0]))
    v = ChatView()
    for i in range(9):
        v._failed_image(f"https://cdn.invalid/{i}")
    assert list(v.failed) == [f"https://cdn.invalid/{i}" for i in range(4, 9)]
    changed = []
    v.image_failed.connect(changed.append)
    now[0] += chat_view.IMAGE_RETRY_SECONDS + 1
    v.pending.update({"https://slow.invalid/1", "https://slow.invalid/2"})
    v._image_retry.stop()
    v._retry_images()
    assert len(v.failed) == 5 and not changed and v._image_retry.interval() >= 100
    v.pending.clear()
    v._image_retry.stop()
    v._retry_images()
    assert len(v.failed) == 3 and len(changed) == 2 and v._image_retry.isActive()
    v.close()
