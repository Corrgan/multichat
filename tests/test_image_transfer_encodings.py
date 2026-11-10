# SPDX-License-Identifier: GPL-3.0-or-later
"""Images must arrive however the server frames the response.

Qt hands a reply over in two ways: a zero-copy buffer (plain HTTP/1.1 with a
Content-Length) or its normal read buffer (chunked, compressed, HTTP/2 – what the emote
CDNs send). Only the first was covered by the other loopback tests; with the second,
every image was dropped after it had arrived, and no emote was shown at all.
"""
import gzip
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtTest import QTest

from multichat.ui.chat_view import ChatView, IMAGE_RES
from multichat.ui.image_io import ImageTransfers
from test_image_limits import png
from test_gui import app  # noqa: F401 – shared QApplication fixture


@pytest.fixture
def server():
    payload = png()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            if self.path.startswith("/length"):
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif self.path.startswith("/gzip"):
                body = gzip.compress(payload)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif self.path.startswith("/pieces"):  # several chunks, the last ones after a pause
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                half = len(payload) // 2
                self.wfile.write(b"%x\r\n%s\r\n" % (half, payload[:half]))
                self.wfile.flush()
                time.sleep(0.05)
                rest = payload[half:]
                self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(rest), rest))
            elif self.path.startswith("/big"):  # chunked and larger than the limit
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                block = b"x" * 4096
                try:
                    for _n in range(64):
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(block), block))
                    self.wfile.write(b"0\r\n\r\n")
                except OSError:
                    pass  # the client gives up at its limit
            else:  # /chunked
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(payload), payload))

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", payload
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)


def wait_for(app, done, seconds=5):
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(5)
    assert done()


@pytest.mark.parametrize("path", ["length", "chunked", "pieces", "gzip"])
def test_image_arrives_complete_with_every_response_framing(app, server, path):
    base, payload = server
    transfers = ImageTransfers(None, max_active=4, max_waiting=8, max_bytes=1_000_000)
    loaded, failed = {}, []
    transfers.loaded.connect(lambda key, data: loaded.__setitem__(key, bytes(data)))
    transfers.failed.connect(failed.append)
    url = f"{base}/{path}.png"
    assert transfers.request(url)
    wait_for(app, lambda: loaded or failed)
    assert failed == [] and loaded == {url: payload}
    transfers.shutdown()


def test_size_limit_still_applies_without_a_content_length(app, server):
    base, _payload = server
    transfers = ImageTransfers(None, max_active=4, max_waiting=8, max_bytes=10_000)
    loaded, failed = [], []
    transfers.loaded.connect(lambda key, _data: loaded.append(key))
    transfers.failed.connect(failed.append)
    url = f"{base}/big.png"
    assert transfers.request(url)
    wait_for(app, lambda: loaded or failed)
    assert loaded == [] and failed == [url]
    transfers.shutdown()


def test_chat_shows_an_image_from_a_chunked_response(app, server):
    base, _payload = server
    view = ChatView()
    url = f"{base}/chunked.png"
    view.append_many([f'<img src="{url}">'])
    view.show()
    view.document().resource(IMAGE_RES, QUrl(url))
    wait_for(app, lambda: url in view.cache or url in view.failed)
    assert url in view.cache and url not in view.failed
    view.close()
