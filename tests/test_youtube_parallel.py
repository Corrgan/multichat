# SPDX-License-Identifier: GPL-3.0-or-later
"""Real YouTube fallback processors must keep messages in their originating GUI group."""
import inspect
import threading
import time

import pytest
import pytchat
from pytchat.core.pytchat import PytchatCore
from pytchat.processors.default.processor import DefaultProcessor
from PyQt6.QtWidgets import QApplication

from multichat.chat import youtube_innertube
from multichat.core.config import clean_groups, load_config
from multichat.ui import chat_view, main_window


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for(app, predicate, timeout=4):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate(), "expected message did not reach its GUI group"


@pytest.mark.parametrize("mode", ["pytchat", "auto"])
def test_parallel_gui_readers_keep_independent_processors_and_message_authors(app, monkeypatch, tmp_path, mode):
    """Two legal overlapping parses, without replacing the parser/renderer or bypassing a lock."""
    videos = ("abcdefghijk", "lmnopqrstuv")
    entered = {vid: threading.Event() for vid in videos}
    release = {vid: threading.Event() for vid in videos}
    traces = {}
    lines, first = inspect.getsourcelines(DefaultProcessor._parse)
    after_setitem = first + next(i for i, line in enumerate(lines) if "renderer.settype()" in line)

    def trace(frame, event, arg):
        if frame.f_code is DefaultProcessor._parse.__code__ and event == "line" and frame.f_lineno == after_setitem:
            vid = frame.f_locals["item"]["liveChatTextMessageRenderer"]["id"]
            if vid in entered and vid not in traces:
                traces[vid] = {
                    "thread": threading.get_ident(), "processor": id(frame.f_locals["self"]),
                    "renderer": id(frame.f_locals["renderer"]),
                }
                entered[vid].set()
                assert release[vid].wait(10), "test did not release the parser barrier"
        return trace

    def response(self, *args, **kwargs):
        vid = self._video_id
        renderer = {
            "id": vid, "authorName": {"simpleText": f"viewer-{vid}"},
            "authorExternalChannelId": f"author-{vid}",
            "authorPhoto": {"thumbnails": [{"url": "https://example.invalid/0"}, {"url": "https://example.invalid/1"}]},
            "timestampUsec": str(int(time.time() * 1_000_000)),
            "message": {"runs": [{"text": f"message-{vid}"}]},
        }
        return {"continuationContents": {"liveChatContinuation": {
            "continuations": [{"timedContinuationData": {"continuation": f"next-{vid}", "timeoutMs": 0}}],
            "actions": [{"addChatItemAction": {"item": {"liveChatTextMessageRenderer": renderer}}}],
        }}}

    monkeypatch.setenv("MULTICHAT_YT_CHAT", mode)
    monkeypatch.setattr(main_window, "CONF_DIR", tmp_path)
    monkeypatch.setattr(main_window, "load_mpv", lambda: None)
    monkeypatch.setattr(chat_view, "_allowed_url", lambda _url: False)  # no remote platform images
    monkeypatch.setattr(pytchat.core.pytchat.util, "get_channelid", lambda client, vid: f"channel-{vid}")
    monkeypatch.setattr(PytchatCore, "_get_livechat_json", response)
    config = load_config()
    config.update(groups=clean_groups(None, {}), extra_emotes=False, check_updates=False,
                  active_group=0, side_by_side=False)
    config["video"]["enabled"] = False
    config["overlay"]["enabled"] = False
    w = main_window.MainWindow(config)
    w.show()
    pages = []

    if mode == "auto":
        class ChangedPage:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def text(self):
                return "<html>live-chat response no longer contains ytInitialData</html>"

        class Session:
            def __init__(self, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            def get(self, url, *, params):
                pages.append(params["v"])
                return ChangedPage()

        # Only the HTTP boundary changes: real InnerTube run/parse_page select the fallback.
        monkeypatch.setattr(youtube_innertube.aiohttp, "ClientSession", Session)

    previous_trace = threading.gettrace()
    threading.settrace(trace)
    try:
        group_a = w.panels[0]
        group_a.rows["youtube"].edit.setText(videos[0])
        group_b = w.add_group()
        group_b.rows["youtube"].edit.setText(videos[1])
        group_a.connect_btn.click()
        assert entered[videos[0]].wait(3)
        group_b.connect_btn.click()
        assert entered[videos[1]].wait(3)
        assert traces[videos[0]]["thread"] != traces[videos[1]]["thread"]
        for vid in videos:
            release[vid].set()
            wait_for(app, lambda vid=vid: any(m.source == f"youtube:{vid}" for m in w.messages.values()))
            received = next(m for m in w.messages.values() if m.source == f"youtube:{vid}")
            assert received.channel == vid
            assert received.user_id == f"author-{vid}"
            assert received.user == f"viewer-{vid}"
            assert received.text == f"message-{vid}"
        assert traces[videos[0]]["processor"] != traces[videos[1]]["processor"]
        assert traces[videos[0]]["renderer"] != traces[videos[1]]["renderer"]
        w._render_now()
        assert f"message-{videos[0]}" in group_a.view.toPlainText()
        assert f"message-{videos[1]}" not in group_a.view.toPlainText()
        assert f"message-{videos[1]}" in group_b.view.toPlainText()
        assert f"message-{videos[0]}" not in group_b.view.toPlainText()
        assert pages == (list(videos) if mode == "auto" else [])
    finally:
        for gate in release.values():
            gate.set()
        threading.settrace(previous_trace)
        w.close()
