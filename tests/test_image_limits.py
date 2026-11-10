# SPDX-License-Identifier: GPL-3.0-or-later
"""Controlled real QNetworkReply/QImageReader boundaries: sizes, concurrency and recovery."""
import struct
import zlib

import pytest
from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
from PyQt6.QtGui import QImage, QImageReader, QMovie
from PyQt6.QtNetwork import QNetworkReply, QNetworkRequest
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QApplication, QLineEdit

from multichat.ui import chat_view, emote_complete, image_io
from multichat.ui.image_io import ImageTransfers


class ControlledReply(QNetworkReply):
    """Real Qt IO semantics with explicitly injected chunks and terminal signals."""

    def __init__(self, parent=None, request=None):
        super().__init__(parent)
        self.buffer = bytearray()
        self.received = 0
        self.aborted = False
        self.done = False
        self.on_done = lambda: None
        self.read_calls = 0
        self.setOpenMode(QIODevice.OpenModeFlag.ReadOnly)
        if request is not None:
            self.setUrl(request.url())

    def readData(self, maximum):
        self.read_calls += 1
        out = bytes(self.buffer[:maximum])
        del self.buffer[:maximum]
        return out

    def bytesAvailable(self):
        return len(self.buffer) + super().bytesAvailable()

    def feed(self, data, *, total=-1, progress=True):
        if self.done:
            return
        self.buffer.extend(data)
        self.received += len(data)
        if progress:
            self.downloadProgress.emit(self.received, total)
        if not self.done:
            self.readyRead.emit()

    def announce_size(self, count):
        self.setHeader(QNetworkRequest.KnownHeaders.ContentLengthHeader, count)
        self.metaDataChanged.emit()

    def complete(self):
        if self.done:
            return
        self.done = True
        self.setFinished(True)
        self.on_done()
        self.finished.emit()

    def abort(self):
        if self.done:
            return
        self.aborted = True
        self.setError(QNetworkReply.NetworkError.OperationCanceledError, "controlled abort")
        self.complete()


class ReplyFactory:
    def __init__(self, manager):
        self.manager = manager
        self.requests, self.replies = [], []
        self.inflight = 0
        self.peak = 0

    def get(self, request):
        self.requests.append(request)
        reply = ControlledReply(self.manager, request)
        self.replies.append(reply)
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        reply.on_done = lambda: setattr(self, "inflight", self.inflight - 1)
        return reply


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def png(size=16):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(0xffff0000)
    data = QByteArray()
    device = QBuffer(data)
    device.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(device, "PNG")
    return bytes(data)


GIF2 = bytes.fromhex(
    "47494638396108000800810000ff000000000000000000000021ff0b4e45545343415045322e30030100000021f904000a0000002c000000000800080000080f0001081c48b0a0c18308132a4c18100021f904010a0001002c0000000008000800810000ff000000000000000000080f0001081c48b0a0c18308132a4c1810003b"
)


def test_transfer_limit_precedes_nam_and_host_queue_is_bounded_and_fair(app, monkeypatch):
    parent = QLineEdit()
    transfers = ImageTransfers(parent, max_active=1, max_waiting=4, max_bytes=1000)
    factory = ReplyFactory(transfers.nam)
    monkeypatch.setattr(transfers.nam, "get", factory.get)
    assert transfers.request("https://a.invalid/active")
    for key in ("https://a.invalid/1", "https://a.invalid/2", "https://b.invalid/1", "https://c.invalid/1"):
        assert transfers.request(key)
    assert not transfers.request("https://d.invalid/overflow")
    assert len(factory.requests) == 1 and len(transfers._queued) == 4
    order = []
    while transfers.active:
        key = next(iter(transfers.active))
        order.append(key)
        transfers.active[key].reply.feed(b"ok")
        transfers.active[key].reply.complete()
    assert order == ["https://a.invalid/active", "https://a.invalid/1", "https://b.invalid/1",
                     "https://c.invalid/1", "https://a.invalid/2"]
    assert factory.peak == 1
    assert all(r.readBufferSize() == image_io.READ_BUFFER_BYTES for r in factory.replies)
    parent.close()


@pytest.mark.parametrize("cause", ["header", "stream", "progress"])
def test_oversized_transfer_aborts_before_unbounded_read(app, monkeypatch, cause):
    parent = QLineEdit()
    transfers = ImageTransfers(parent, max_active=1, max_waiting=1, max_bytes=100)
    factory = ReplyFactory(transfers.nam)
    monkeypatch.setattr(transfers.nam, "get", factory.get)
    failures, loaded = [], []
    transfers.failed.connect(failures.append)
    transfers.loaded.connect(lambda *data: loaded.append(data))
    transfers.request("https://a.invalid/image")
    reply = factory.replies[0]
    if cause == "header":
        reply.announce_size(101)
        assert reply.read_calls == 0
    elif cause == "stream":
        reply.feed(b"x" * 80, progress=False)
        assert transfers.active["https://a.invalid/image"].data.size() <= 100
        reply.feed(b"x" * 30, progress=False)
    else:
        reply.downloadProgress.emit(101, -1)
    assert reply.aborted and not transfers.active and not loaded
    assert failures == ["https://a.invalid/image"]
    parent.close()


def test_wall_deadline_releases_stalled_slot_despite_trickling_peer(app, monkeypatch):
    parent = QLineEdit()
    transfers = ImageTransfers(parent, max_active=1, max_waiting=1, max_bytes=100, timeout_ms=40)
    factory = ReplyFactory(transfers.nam)
    monkeypatch.setattr(transfers.nam, "get", factory.get)
    transfers.request("https://a.invalid/slow")
    transfers.request("https://b.invalid/next")
    first = factory.replies[0]
    for _ in range(6):
        first.feed(b"x")
        QTest.qWait(10)
    assert first.aborted and len(factory.requests) == 2 and factory.peak == 1
    assert factory.requests[0].transferTimeout() == 40
    factory.replies[-1].abort()
    parent.close()


@pytest.mark.parametrize("io_failure", [False, True])
def test_network_and_qiodevice_read_errors_are_terminal_without_qt_slot_exception(app, monkeypatch, io_failure):
    parent = QLineEdit()
    transfers = ImageTransfers(parent, max_active=1, max_waiting=1, max_bytes=100)
    factory = ReplyFactory(transfers.nam)
    monkeypatch.setattr(transfers.nam, "get", factory.get)
    failed = []
    transfers.failed.connect(failed.append)
    key = "https://a.invalid/failed"
    transfers.request(key)
    reply = factory.replies[0]
    if io_failure:
        monkeypatch.setattr(reply, "read", lambda *_: None)  # Qt IO error (-1), as exposed by PyQt on Linux
        reply.readyRead.emit()
    else:
        reply.setError(QNetworkReply.NetworkError.ConnectionRefusedError, "controlled connection failure")
        monkeypatch.setattr(reply, "read", lambda *_: pytest.fail("do not read an already failed reply"))
        reply.complete()
    assert not transfers.active and failed == [key]
    parent.close()


def test_chat_and_icons_bound_active_and_waiting_requests_before_qt(app, monkeypatch):
    view = chat_view.ChatView()
    factory = ReplyFactory(view.nam)
    monkeypatch.setattr(view.nam, "get", factory.get)
    for i in range(2000):
        view.loadResource(chat_view.IMAGE_RES, QUrl(f"https://cdn.invalid/{i}"))
    assert len(factory.requests) == chat_view.MAX_IMAGE_ACTIVE
    assert len(view._images._queued) == chat_view.MAX_IMAGE_WAITING
    assert len(view.pending) == chat_view.MAX_IMAGE_ACTIVE + chat_view.MAX_IMAGE_WAITING
    assert len(view.failed) <= chat_view.MAX_FAILED
    assert len(view._failure_notifications) <= chat_view.MAX_FAILED
    edit = QLineEdit()
    completer = emote_complete.EmoteCompleter(edit, lambda: {})
    icons = ReplyFactory(completer._net)
    monkeypatch.setattr(completer._net, "get", icons.get)
    for i in range(200):
        completer._icon(f"https://icons.invalid/{i}")
    assert len(icons.requests) == emote_complete.MAX_ICON_ACTIVE
    assert len(completer._images._queued) == emote_complete.MAX_ICON_WAITING
    assert len(completer._loading) == emote_complete.MAX_ICON_ACTIVE + emote_complete.MAX_ICON_WAITING
    # Complete every accepted image; queued work continues, active peak stays bounded.
    payload = png()
    while completer._images.active:
        state = next(iter(completer._images.active.values()))
        state.reply.feed(payload)
        state.reply.complete()
    assert icons.peak == emote_complete.MAX_ICON_ACTIVE
    assert not completer._loading and len(completer.icons) == 70
    # This leaves no fake deferred operations running after the monkeypatch is removed.
    view._images.waiting.clear()
    view._images._queued.clear()
    for state in list(view._images.active.values()):
        state.reply.abort()
    view._failure_timer.stop()
    view._image_retry.stop()
    view.close()
    edit.close()


def test_pixel_header_is_rejected_before_any_decode_for_chat_and_icons(app, monkeypatch):
    # Tiny PNG whose header advertises a 16MP raster: no large test allocation needed.
    ihdr = struct.pack(">IIBBBBB", 4096, 4096, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + ihdr
    huge = QByteArray(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(ihdr)) + chunk
                     + struct.pack(">I", zlib.crc32(chunk)) + b"\x00\x00\x00\x00IEND\xaeB`\x82")
    decoded = []
    monkeypatch.setattr(QImageReader, "read", lambda *_: decoded.append(True))
    view = chat_view.ChatView()
    assert view._decode("https://cdn.invalid/huge", huge) is None
    edit = QLineEdit()
    completer = emote_complete.EmoteCompleter(edit, lambda: {})
    completer._loaded("https://cdn.invalid/huge", huge)
    assert not decoded and not completer.icons
    view.close()
    edit.close()


@pytest.mark.parametrize("declared_frames", [0, chat_view.MAX_ANIMATION_FRAMES + 1])
def test_unknown_or_excessive_animation_frames_use_static_first_frame(app, monkeypatch, declared_frames):
    monkeypatch.setattr(QImageReader, "imageCount", lambda *_: declared_frames)
    view = chat_view.ChatView()
    image = view._decode("https://cdn.invalid/a.gif", QByteArray(GIF2))
    assert image is not None and not view.movies
    view.close()


def test_normal_animation_caches_frames_and_total_animation_bytes_are_bounded(app, monkeypatch):
    monkeypatch.setattr(chat_view, "MAX_MOVIE_BYTES", len(GIF2))
    view = chat_view.ChatView()
    first = view._decode("https://cdn.invalid/a.gif", QByteArray(GIF2))
    second = view._decode("https://cdn.invalid/b.gif", QByteArray(GIF2))
    assert first is not None and second is not None
    assert len(view.movies) == 1 and view._movie_bytes == len(GIF2)
    movie, _buffer = view.movies["https://cdn.invalid/a.gif"]
    # an ordinary emote keeps its decoded frames (a third of the CPU of decoding each again)
    assert movie.cacheMode() == QMovie.CacheMode.CacheAll and movie.frameCount() == 2
    assert view._frame_cache_bytes == view._frame_cache["https://cdn.invalid/a.gif"] > 0
    view.set_font_px(view.font_px + 1)
    assert not view.movies and view._movie_bytes == 0 and view._frame_cache_bytes == 0
    view.close()


def test_frame_cache_has_a_budget(app, monkeypatch):
    """Animations that don't fit the frame-cache budget still play, decoded frame by frame."""
    view = chat_view.ChatView()
    monkeypatch.setattr(chat_view, "MAX_FRAME_CACHE_PER_MOVIE", 1)  # nothing fits
    assert view._decode("https://cdn.invalid/big.gif", QByteArray(GIF2)) is not None
    movie, _buffer = view.movies["https://cdn.invalid/big.gif"]
    assert movie.cacheMode() == QMovie.CacheMode.CacheNone and view._frame_cache_bytes == 0
    monkeypatch.setattr(chat_view, "MAX_FRAME_CACHE_PER_MOVIE", 2 * 1024 * 1024)
    assert view._decode("https://cdn.invalid/a.gif", QByteArray(GIF2)) is not None
    one = view._frame_cache_bytes
    monkeypatch.setattr(chat_view, "MAX_FRAME_CACHE_BYTES", one)  # the total budget is used up
    assert view._decode("https://cdn.invalid/b.gif", QByteArray(GIF2)) is not None
    assert view.movies["https://cdn.invalid/b.gif"][0].cacheMode() == QMovie.CacheMode.CacheNone
    assert view._frame_cache_bytes == one
    view._drop_movie("https://cdn.invalid/a.gif")
    assert view._frame_cache_bytes == 0
    view.close()


def test_fast_animation_is_paced_and_hidden_movies_stop_resuming(app):
    data = bytearray(GIF2)
    position = 0
    while (position := data.find(b"\x21\xf9\x04", position)) >= 0:
        data[position + 4:position + 6] = b"\x02\x00"  # real GIF frame delay: 20ms
        position += 8
    view = chat_view.ChatView()
    key = "https://cdn.invalid/fast.gif"
    image = view._decode(key, QByteArray(bytes(data)))
    view._store(key, image)
    movie, _buffer = view.movies[key]
    assert movie.state() == QMovie.MovieState.NotRunning  # loading alone never starts offscreen decoding
    changes = QSignalSpy(movie.frameChanged)
    view.append_many([f"<img src='{key}'>"])
    view.show()
    app.processEvents()
    view.update_animations()
    QTest.qWait(250)
    assert 2 <= len(changes) <= 7  # actual Qt frame advancement is capped near 20fps
    view.hide()
    app.processEvents()
    before = len(changes)
    QTest.qWait(100)
    assert len(changes) == before and not view._playing
    assert not view._movie_resume[key].isActive()
    view.close()


def test_only_visible_animation_budget_starts_movies(app, monkeypatch):
    monkeypatch.setattr(chat_view, "MAX_ANIMATED", 1)
    view = chat_view.ChatView()
    keys = [f"https://cdn.invalid/{i}.gif" for i in range(2)]
    for key in keys:
        view._store(key, view._decode(key, QByteArray(GIF2)))
    assert all(movie.state() == QMovie.MovieState.NotRunning for movie, _buffer in view.movies.values())
    view.append_many([" ".join(f"<img src='{key}'>" for key in keys)])
    view.show()
    app.processEvents()
    view.update_animations()
    assert len(view._playing) == 1
    assert sum(movie.state() == QMovie.MovieState.Running for movie, _buffer in view.movies.values()) <= 1
    view.close()


def test_gif_small_canvas_cannot_hide_oversized_frame_geometry(app, monkeypatch):
    data = bytearray(GIF2)
    descriptor = data.index(0x2C)
    data[descriptor + 5:descriptor + 7] = struct.pack("<H", 4096)
    decoded = []
    monkeypatch.setattr(QImageReader, "read", lambda *_: decoded.append(True))
    view = chat_view.ChatView()
    assert view._decode("https://cdn.invalid/bad.gif", QByteArray(bytes(data))) is None
    assert not decoded and not view.movies
    view.close()


def test_animation_source_working_budget_and_frame_pixel_budget_fall_back_to_static(app, monkeypatch):
    view = chat_view.ChatView()
    monkeypatch.setattr(chat_view, "MAX_MOVIE_WORKING_BYTES", 1)
    assert view._decode("https://cdn.invalid/working.gif", QByteArray(GIF2)) is not None
    assert not view.movies
    monkeypatch.setattr(chat_view, "MAX_MOVIE_WORKING_BYTES", 32 * 1024 * 1024)
    monkeypatch.setattr(chat_view, "MAX_ANIMATION_PIXELS", 1)
    assert view._decode("https://cdn.invalid/frames.gif", QByteArray(GIF2)) is not None
    assert not view.movies
    view.close()


def test_vector_formats_are_rejected_before_geometry_parse(app, monkeypatch):
    monkeypatch.setattr(QImageReader, "format", lambda *_: QByteArray(b"svg"))
    sizes = []
    monkeypatch.setattr(QImageReader, "size", lambda *_: sizes.append(True))
    assert image_io.raster_reader(QByteArray(b"<svg>untrusted</svg>")) is None
    assert not sizes


def test_positive_image_cache_also_evicts_document_resources_by_bytes(app, monkeypatch):
    monkeypatch.setattr(chat_view, "MAX_CACHE_BYTES", 4 * 4 * 4 * 2)
    view = chat_view.ChatView()
    for i in range(5):
        view._store(f"https://cdn.invalid/{i}", QImage(4, 4, QImage.Format.Format_ARGB32))
    assert len(view.cache) == 2 and view.cache.byte_size == chat_view.MAX_CACHE_BYTES
    # Invalidated document resource asks the network boundary again instead of retaining the image.
    factory = ReplyFactory(view.nam)
    monkeypatch.setattr(view.nam, "get", factory.get)
    resource = view.document().resource(chat_view.IMAGE_RES, QUrl("https://cdn.invalid/0"))
    assert resource.size() == view.placeholder.size() and len(factory.requests) == 1
    factory.replies[0].abort()
    view.close()
