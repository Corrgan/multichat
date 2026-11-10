# SPDX-License-Identifier: GPL-3.0-or-later
"""Real loopback Qt replies must stop before closed image owners are collected."""
import gc
import threading
import time
import weakref
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PyQt6.QtCore import QByteArray, QCoreApplication, QEvent, QUrl
from PyQt6.QtGui import QMovie
from PyQt6.QtTest import QSignalSpy, QTest
from PyQt6.QtWidgets import QLineEdit

from multichat.ui.chat_view import ChatView, IMAGE_RES
from multichat.ui.emote_complete import EmoteCompleter
from test_image_limits import GIF2, png


def wait_until(app, predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert predicate()


@pytest.fixture
def stalled_images():
    release = threading.Event()
    requested = []
    payload = png()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            try:
                self.wfile.write(payload[:16])
                self.wfile.flush()
                release.wait(5)
                self.wfile.write(payload[16:])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # Client shutdown intentionally aborts this real stalled reply.

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", release, requested
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_real_chat_close_aborts_reply_discards_queue_and_stops_animation(_session_qapplication, stalled_images):
    app = _session_qapplication
    base, release, requested = stalled_images
    view = ChatView()
    transfers = view._images
    transfers.max_active = 1
    first, second = base + "/active.png", base + "/queued.png"
    view.document().resource(IMAGE_RES, QUrl(first))
    view.document().resource(IMAGE_RES, QUrl(second))
    wait_until(app, lambda: first in transfers.active and transfers.active[first].data.size() == 16)
    state = transfers.active[first]
    assert transfers._queued == {second} and requested == ["/active.png"]

    movie_key = "https://cdn.invalid/retained-animation.gif"
    frame = view._decode(movie_key, QByteArray(GIF2))
    assert frame is not None
    view._store(movie_key, frame)
    view.append_many([f'<img src="{movie_key}">'])
    view.show()
    app.processEvents()
    view.update_animations()
    movie, _buffer = view.movies[movie_key]
    assert movie.state() != QMovie.MovieState.NotRunning
    view._failed_image("https://cdn.invalid/previous-failure.png")
    assert view._image_retry.isActive()
    delivered = [QSignalSpy(transfers.loaded), QSignalSpy(transfers.failed), QSignalSpy(view.image_failed)]

    view.close()  # Actual QWidget close path; no network method or Qt signal is mocked.
    assert not transfers.active and not transfers.waiting and not transfers._queued
    view.shutdown()  # Repeated shutdown must be harmless.
    assert not view.pending and not view.failed and not view._failure_notifications
    assert state.data.isEmpty() and not state.timer.isActive() and not state.connections
    assert not view.movies and movie.state() == QMovie.MovieState.NotRunning
    assert all(not timer.isActive() for timer in
               (view._failure_timer, view._image_retry, view._relayout, view._repaint, view._anim))
    assert not transfers.request(base + "/after-close.png")
    release.set()
    for _ in range(5):
        app.processEvents()
        QTest.qWait(5)
    assert all(len(spy) == 0 for spy in delivered)
    assert requested == ["/active.png"]

    owner_ref, transfer_ref = weakref.ref(view), weakref.ref(transfers)
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    del state, movie, _buffer, delivered, view, transfers
    gc.collect()
    app.processEvents()
    assert owner_ref() is None and transfer_ref() is None


def test_completer_shutdown_stops_real_pending_transfer_and_edit_callbacks(_session_qapplication, stalled_images):
    app = _session_qapplication
    base, release, requested = stalled_images
    edit = QLineEdit()
    completer = EmoteCompleter(edit, lambda: {"Kappa": "https://cdn.invalid/kappa"})
    # The transfer component accepts loopback HTTP; public icon URLs remain HTTPS.
    transfers = completer._images
    transfers.max_active = 1
    first, second = base + "/icon-active.png", base + "/icon-queued.png"
    assert transfers.request(first) and transfers.request(second)
    wait_until(app, lambda: first in transfers.active and transfers.active[first].data.size() == 16)
    state = transfers.active[first]
    signals = [QSignalSpy(transfers.loaded), QSignalSpy(transfers.failed)]
    edit.close()
    assert not transfers.active and not transfers._queued and state.data.isEmpty()
    completer.shutdown()
    assert not state.connections and not state.timer.isActive() and not completer._connections
    assert completer.provider() == {}
    release.set()
    QTest.keyClicks(edit, ":kap")
    app.processEvents()
    assert not completer.visible()
    assert all(len(spy) == 0 for spy in signals)
    assert requested == ["/icon-active.png"]
    edit.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
