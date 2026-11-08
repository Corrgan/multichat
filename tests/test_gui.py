# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window without network: chat rendering, filters, moderation menu, languages."""
import asyncio

import pytest
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox

from multichat.chat.kick import kick_parts
from multichat.chat.models import ChatMessage, Deletion
from multichat.chat.twitch import twitch_parts
from multichat.core import i18n
from multichat.core.config import clean_groups, load_config
from multichat.ui import main_window
from multichat.ui.dialogs import AboutDialog

KAPPA = "https://static-cdn.jtvnw.net/emoticons/v2/25/default/dark/2.0"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    async def idle(value, emit, status, emotes=None):
        status("twitch", "connected (#{channel})", channel=value)
        await asyncio.sleep(3600)

    from multichat.ui import window_connections

    for name in ("run_twitch", "run_kick", "run_youtube"):
        monkeypatch.setattr(window_connections, name, idle)
    cfg = load_config()
    cfg["video"]["enabled"] = False
    cfg["extra_emotes"] = False
    cfg["groups"] = clean_groups(None, {})  # earlier tests saved their channels in the shared settings
    cfg["active_group"], cfg["side_by_side"] = 0, False
    w = main_window.MainWindow(cfg)
    yield w
    w.close()
    i18n.set_language("en")


def test_chat_and_moderation_menu(window):
    w = window
    img = QImage(28, 28, QImage.Format.Format_ARGB32)
    img.fill(QColor("#f5c542"))
    w.view.cache[KAPPA] = img
    w.add_message(ChatMessage("twitch", "chan", "Alice", "Kappa hi <b>world</b>",
                              parts=twitch_parts("Kappa hi <b>world</b>", "25:0-4"), color="#FF4500", msg_id="m1", user_id="1"))
    w.add_message(ChatMessage("kick", "chan", "Bob", "hey KEKW", parts=kick_parts("hey [emote:1:KEKW]"), msg_id="k1", user_id="2"))
    w.add_message(ChatMessage("youtube", "vid", "Carol", "greetings", msg_id="y1", user_id="UC3"))
    troll = ChatMessage("twitch", "chan", "Troll", "spam spam", msg_id="m2", user_id="9")
    w.add_message(troll)
    w._flush_messages()  # messages are shown in batches
    alice, bob = list(w.messages.values())[:2]
    assert "<b>world</b>" in w.view.toPlainText()  # HTML in messages is shown as text
    w.apply_deletion(Deletion("twitch", user_id="9"))
    assert troll.deleted and not alice.deleted
    w.filters["kick"].setChecked(False)
    w._render_now()
    assert "Bob" not in w.view.toPlainText()
    w.filters["kick"].setChecked(True)
    w._render_now()
    assert "Bob" in w.view.toPlainText()

    shown = []
    QMenu.exec = lambda self, *a: shown.append([x.text() for x in self.actions()])
    w.on_name_context(QUrl(f"msg:{alice.uid}"))  # right click: menu
    assert any(t.startswith("Log in to Twitch") for t in shown[0])
    w.accounts["twitch"].store.update("twitch", access_token="x", expires_at=9e12, user_name="TestUser")
    w.refresh_accounts()
    assert not w.input.isEnabled()  # logged in, but no Twitch channel in this group yet
    w.panels[0].rows["twitch"].edit.setText("chan")
    assert w.input.isEnabled() and w.target.count() == 1
    w.on_name_context(QUrl(f"msg:{alice.uid}"))
    assert "Delete message" in shown[1] and "Ban …" in shown[1] and "Unban" in shown[1]
    w.mention(bob)
    assert w.input.text().startswith("@Bob")
    w.logout("twitch")


def test_selection_action_bar_and_user_card(window, app, monkeypatch):
    w = window
    answers = {"chan": False, "ownchan": None}

    async def is_mod(channel):
        return answers[channel]

    async def info(msg):
        return {"created": "2020-01-01T00:00:00Z", "following_since": ""}

    acc = w.accounts["twitch"]
    monkeypatch.setattr(acc, "is_moderator", is_mod)
    monkeypatch.setattr(acc, "user_info", info)
    acc.store.update("twitch", access_token="x", expires_at=9e12, user_name="Me", user_id="42")
    w.refresh_accounts()

    def settle():
        for _ in range(30):
            app.processEvents()
            asyncio.run(asyncio.sleep(0.01))

    first = ChatMessage("twitch", "chan", "Alice", "first", msg_id="a1", user_id="1")
    w.add_message(first)
    w.add_message(ChatMessage("twitch", "chan", "Bob", "other", msg_id="b1", user_id="2"))
    w._flush_messages()
    settle()
    bar = w.panels[0].action_bar
    assert not bar.card.isEnabled()  # nothing selected yet
    w.on_anchor(QUrl(f"msg:{first.uid}"))  # left click selects
    assert w.mod.selected is first and bar.card.isEnabled()
    other = w.add_group()  # the other group's bar stays empty: Alice chats in the first group
    assert not other.action_bar.card.isEnabled() and bar.isVisibleTo(w.panels[0])
    w.tabs.setCurrentIndex(0)
    # Twitch said: not a moderator in #chan → mod buttons hidden, menu says so
    assert w.mod.is_mod(first) is False and bar.ban.isHidden() and bar.delete.isHidden()
    shown = []
    QMenu.exec = lambda self, *a: shown.append([x.text() for x in self.actions()])
    w.on_name_context(QUrl(f"msg:{first.uid}"))
    assert "You are not a moderator in this channel" in shown[0] and "Ban …" not in shown[0]
    # your own message with a moderator badge wins over the API answer
    w.add_message(ChatMessage("twitch", "chan", "Me", "hi", user_id="42", badges=("moderator",)))
    w._flush_messages()
    settle()
    assert w.mod.is_mod(first) is True and not bar.ban.isHidden() and bar.ban.isEnabled()

    # newer message of the selected user → actions use it
    second = ChatMessage("twitch", "chan", "Alice", "second", msg_id="a2", user_id="1")
    w.add_message(second)
    w._flush_messages()
    assert w.mod.selected is second and w.mod.last_deletable(first) is second
    assert "background:#3a3a55" in w.view.message_html(second)  # selected name is highlighted

    done = []

    async def fake_delete(msg):
        done.append(msg.msg_id)

    monkeypatch.setattr(acc, "delete", fake_delete)
    bar.delete.click()
    settle()
    assert done == ["a2"] and second.deleted
    w.mod.open_card(second)
    card = w.mod.card
    assert card.isVisible() and card.info_labels["seen"].text() == "2"  # filled right away
    assert "second" in card.history.toPlainText()
    settle()
    assert card.info_labels["created"].text().startswith("2020-01-01")
    assert card.info_labels["following"].text() == "not following"
    assert "delete: second" in card.actions.text()
    card.notes.setPlainText("watch this one")
    w.mod.notes.save()
    from multichat.ui.moderation import ModNotes
    again = ModNotes(w.mod.notes.path)
    assert again.get(second)["note"] == "watch this one"
    assert again.get(second)["actions"][-1]["a"].startswith("delete")
    bar.clear.click()
    assert w.mod.selected is None and w.view.selected is None
    w.logout("twitch")


def test_font_size(window):
    w = window
    w.add_message(ChatMessage("twitch", "chan", "Alice", "hello"))
    w._flush_messages()
    w.change_font(2)
    assert w.view.font_px == 16 and w.config["chat_font"] == 16 and "font-size:16px" in w.view.styleSheet()
    assert w.view.emote_h > 22
    for _ in range(100):
        w.change_font(2)
    assert w.view.font_px == 40
    w.change_font(0)
    assert w.view.font_px == 14 and w.view.emote_h == 22


def rows(w, group=0):
    return w.panels[group].rows


def test_connect_disconnect(window, app):
    w = window
    rows(w)["twitch"].edit.setText("examplechan")
    w.connect_all()
    status = rows(w)["twitch"].status
    for _ in range(50):
        app.processEvents()
        if status.full.startswith("connected"):
            break
        asyncio.run(asyncio.sleep(0.02))
    assert status.full == "connected (#examplechan)"
    w.disconnect_all()
    app.processEvents()
    assert w.readers == {} and status.full == "disconnected"


def test_single_channel_connect_disconnect(window, app):
    w = window
    r = rows(w)
    r["twitch"].edit.setText("examplechan")
    r["kick"].edit.setText("examplekick")
    panel = w.panels[0]
    w.connect_group(panel)
    assert set(w.readers) == {"twitch:examplechan", "kick:examplekick"}
    assert r["youtube"].button.isEnabled() is False  # nothing entered
    w.toggle_row(r["kick"])  # disconnect only Kick
    app.processEvents()
    assert set(w.readers) == {"twitch:examplechan"} and r["kick"].status.full == "disconnected"
    assert panel.connect_btn.text() == "Reconnect" and panel.disconnect_btn.isEnabled()
    old_gen = w.conn_gen["kick:examplekick"]
    w.toggle_row(r["kick"])  # and back
    assert set(w.readers) == {"twitch:examplechan", "kick:examplekick"}
    assert w.conn_gen["kick:examplekick"] == old_gen + 1
    w.disconnect_all()
    assert w.readers == {} and not panel.disconnect_btn.isEnabled()


def test_several_channels_and_groups(window, app):
    w = window
    first = w.panels[0]
    assert set(first.rows) == {"twitch", "kick", "youtube"}  # one channel per platform
    first.rows["twitch"].edit.setText("twitch.tv/Chan_A")
    first.rows["youtube"].edit.setText("@first_yt")
    assert first.rows["twitch"].key() == "twitch:chan_a"
    assert first.title() == "chan_a" and w.tabs.tabText(0) == "chan_a"  # unnamed: first channel (top to bottom)
    first.rows["twitch"].edit.setText("")
    assert first.title() == "@first_yt"
    first.rows["twitch"].edit.setText("chan_a")

    group_b = w.add_group()
    assert group_b.title() == "Group 2"  # nothing entered yet
    group_b.rows["kick"].edit.setText("examplekick")
    assert w.tabs.tabText(1) == "examplekick"
    group_b.name.setText("Other streamer")
    group_b.rows["twitch"].edit.setText("chan_a")  # also in the first group
    group_c = w.add_group()
    group_c.rows["twitch"].edit.setText("chan_b")  # a second Twitch channel: its own group
    assert [w.tabs.tabText(i) for i in range(w.tabs.count())] == ["chan_a", "Other streamer", "chan_b"]
    w.connect_all()
    assert set(w.readers) == {"twitch:chan_a", "twitch:chan_b", "kick:examplekick", "youtube:@first_yt"}

    # messages go to the groups that contain their channel
    def msg(source, text, **kw):
        platform, channel = source.split(":")
        m = ChatMessage(platform, channel, "User", text, **kw)
        w._emit(m, source)
        return m

    w.tabs.setCurrentIndex(0)
    assert first.isVisibleTo(w) and not group_b.isVisibleTo(w)  # tabs: only the active group
    msg("twitch:chan_b", "only in third")
    msg("kick:examplekick", "only in second @testuser")
    msg("twitch:chan_a", "in both")
    w.accounts["twitch"].store.update("twitch", access_token="x", expires_at=9e12, user_name="TestUser")
    app.processEvents()
    w._flush_messages()
    w._render_timer.stop()
    w._render_now()
    text_a, text_b, text_c = first.view.toPlainText(), group_b.view.toPlainText(), group_c.view.toPlainText()
    assert "only in third" in text_c and "only in third" not in text_a + text_b
    assert "only in second" in text_b and "only in second" not in text_a
    assert "in both" in text_a and "in both" in text_b and "in both" not in text_c
    assert group_b.unread == 2 and group_b.mentions == 1  # counter on the hidden tab
    assert w.tabs.tabText(1) == "Other streamer (@2)"
    w.tabs.setCurrentIndex(1)
    assert group_b.unread == 0 and w.tabs.tabText(1) == "Other streamer"

    # a ban in one Twitch channel leaves the other channel alone
    a1 = msg("twitch:chan_a", "a", user_id="7", msg_id="m1")
    b1 = msg("twitch:chan_b", "b", user_id="7", msg_id="m2")
    app.processEvents()
    w._flush_messages()
    from multichat.chat.models import Deletion as D

    w._emit(D("twitch", user_id="7"), "twitch:chan_a")
    app.processEvents()
    assert a1.deleted and not b1.deleted

    # sending: the active group's channels you're logged in for
    w.tabs.setCurrentIndex(2)
    assert [w.target.itemText(i) for i in range(w.target.count())] == ["Twitch"]
    w.tabs.setCurrentIndex(1)
    assert [w.target.itemText(i) for i in range(w.target.count())] == ["Twitch"]  # Kick: not logged in

    # side by side (chat and videos of each group next to each other) and back
    w.side_action.setChecked(True)
    assert all(pn.isVisibleTo(w) for pn in w.panels) and not w.tabs.isVisibleTo(w)
    assert [w.side.widget(i) for i in range(w.side.count())] == w.panels
    w.side_action.setChecked(False)
    assert w.tabs.count() == 3 and [pn.isVisibleTo(w) for pn in w.panels] == [False, True, False]

    # settings round trip
    w.save_config()
    groups = w.config["groups"]
    assert groups[1]["name"] == "Other streamer" and groups[0]["name"] == ""
    assert [c["channel"] for c in groups[0]["channels"]] == ["chan_a", "@first_yt"]

    # disconnecting a group keeps channels another group still shows
    w.disconnect_group(group_b)
    assert set(w.readers) == {"twitch:chan_a", "twitch:chan_b", "youtube:@first_yt"}
    w.remove_group(group_b) if not group_b.keys() else None
    QMessageBox.question = lambda *a, **k: QMessageBox.StandardButton.Yes
    w.remove_group(group_b)
    w.remove_group(group_c)
    assert len(w.panels) == 1 and w.tabs.count() == 1 and first.isVisibleTo(w)
    w.disconnect_all()
    w.logout("twitch")


def test_old_settings_become_the_first_group(tmp_path):
    from multichat.core.config import clean_groups

    groups = clean_groups(None, {"twitch": "a", "kick": "", "youtube": "@b"})
    assert groups == [{"name": "", "channels": [
        {"platform": "twitch", "channel": "a", "video": True},
        {"platform": "kick", "channel": "", "video": True},
        {"platform": "youtube", "channel": "@b", "video": True}]}]
    bad = clean_groups([{"name": 5, "channels": [{"platform": "twitch", "channel": f"c{i}"} for i in range(9)]
                         + [{"platform": "nope"}, "junk"]}, "junk"], {})
    assert len(bad) == 1 and len(bad[0]["channels"]) == 1 and bad[0]["name"] == ""  # one per platform


@pytest.mark.parametrize("lang,view,help_", [("en", "View", "Help"), ("de", "Ansicht", "Hilfe"),
                                             ("ja", "表示", "ヘルプ"), ("zh_CN", "视图", "帮助")])
def test_languages_and_about(app, monkeypatch, lang, view, help_):
    i18n.set_language(lang)
    cfg = load_config()
    cfg["video"]["enabled"] = False
    cfg["language"] = "auto"
    w = main_window.MainWindow(cfg)
    try:
        menus = [a.text() for a in w.menuBar().actions()]
        assert menus[0] == view and menus[-1] == help_
        langmenu = [a.menu() for a in w.menuBar().actions()[0].menu().actions() if a.menu()][-1]  # last submenu
        assert sum(a.isChecked() for a in langmenu.actions()) == 1 and sum(a.isCheckable() for a in langmenu.actions()) == 7
        asked, restarted = [], []
        monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: (asked.append(a[2]), QMessageBox.StandardButton.No)[1]))
        monkeypatch.setattr(main_window, "restart_app", lambda *a: restarted.append(1))
        w.change_language("fr")
        assert w.config["language"] == "fr" and asked and not restarted
        AboutDialog(w).close()
    finally:
        w.close()
        i18n.set_language("en")


def test_send_403_explains_restrictions(window, monkeypatch):
    from multichat.accounts import ApiError

    async def forbidden(channel, text):
        raise ApiError("Kick (403): Forbidden", status=403)

    monkeypatch.setattr(window.accounts["kick"], "send", forbidden)
    with pytest.raises(ApiError) as e:
        asyncio.run(window.send_to("kick", "examplechan", "hi"))
    assert e.value.status == 403 and "followers" in str(e.value) and "Forbidden" in str(e.value)


def test_batching_keeps_order_and_limit(window):
    w = window
    for i in range(1200):
        w.add_message(ChatMessage("twitch", "chan", f"u{i}", f"m{i}"))
    assert not w.messages  # nothing rendered before the flush
    w._flush_messages()
    assert len(w.messages) == main_window.MAX_MESSAGES
    users = [m.user for m in w.messages.values()]
    assert users[0] == "u200" and users[-1] == "u1199"


def test_channel_input_normalisation():
    n = main_window.MainWindow.normalize_channel
    assert n("twitch", "https://www.twitch.tv/ExampleChan") == "examplechan"
    assert n("twitch", "#examplechan") == "examplechan"
    assert n("kick", "kick.com/example-chan") == "example-chan"
    assert n("twitch", "bad name\r\nPRIVMSG #x :hi") is None
    assert n("kick", "../../api") is None
    assert n("youtube", "@examplechannel") == "@examplechannel"


def test_no_video_when_tiles_are_off(window, monkeypatch):
    started = []
    from multichat.video.tiles import VideoPanel

    monkeypatch.setattr(VideoPanel, "set_entries", lambda self, entries, shown=True: started.append(entries))
    row = window.panels[0].rows["kick"]
    row.edit.setText("examplekick")
    window.toggle_row(row)
    window.toggle_row(row)
    assert started == []  # video tiles are switched off in this window


def test_update_notice_and_login_link(window, app):
    w = window
    w._show_update("9.90", "https://github.com/Corrgan/multichat/releases/tag/v9.90")
    assert any("9.90" in a.text() for a in w.menuBar().actions())
    w._show_login_url("https://id.example/authorize?x=1")
    app.processEvents()
    box = next(b for b in w.findChildren(QMessageBox) if "authorize" in b.informativeText())
    copy = next(b for b in box.buttons() if b.text() == "Copy link")
    copy.click()
    assert QApplication.clipboard().text() == "https://id.example/authorize?x=1"
    box.close()


def test_update_check_builds_its_own_link(window, monkeypatch):
    shown = []
    window.bridge.update.connect(lambda v, url: shown.append((v, url)))

    class Resp:
        def __init__(self, data):
            self.status, self.data = 200, data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self):
            return self.data

    def fake_get(data):
        return lambda *a, **k: Resp(data)

    async def run(data):
        monkeypatch.setattr(window.session, "get", fake_get(data))
        await window._check_update()

    asyncio.run(run({"tag_name": "v9.90", "html_url": "https://evil.example/download"}))
    asyncio.run(run({"tag_name": "v9.99<script>", "html_url": "https://evil.example/"}))
    QApplication.processEvents()
    assert shown == [("9.90", "https://github.com/Corrgan/multichat/releases/tag/v9.90")]


def test_autoscroll_pauses_and_resumes(window, app):
    w = window
    w.resize(500, 400)
    w.show()
    for i in range(80):
        w.add_message(ChatMessage("twitch", "chan", "User", f"line {i}"))
    w._flush_messages()
    app.processEvents()
    view, bar = w.view, w.view.verticalScrollBar()
    assert view.follow and bar.value() == bar.maximum() > 0 and view.jump.isHidden()
    # content growing later (e.g. images loading) keeps it at the bottom
    view.append_many(["<p style='font-size:60px'>tall</p>"])
    app.processEvents()
    assert view.follow and bar.value() == bar.maximum()
    bar.setValue(0)  # the user scrolls up → paused
    assert not view.follow and not view.jump.isHidden()
    w.add_message(ChatMessage("twitch", "chan", "User", "while paused"))
    w._flush_messages()
    assert bar.value() == 0 and "while paused" not in view.toPlainText()
    assert view.unseen == 1 and "(1)" in view.jump.text()
    view.jump.click()  # jump to the newest
    w._render_now()
    app.processEvents()
    assert view.follow and view.jump.isHidden() and bar.value() == bar.maximum()
    assert "while paused" in view.toPlainText()
    bar.setValue(0)
    bar.setValue(bar.maximum())  # scrolling back down by hand also resumes
    assert view.follow


def test_alert_highlight_and_settings(window, monkeypatch):
    from multichat.overlay.server import message_payload
    from multichat.ui import dialogs

    w = window
    w.view.alerts_cfg = dict(w.config["alerts"], min_bits=100)
    sub = ChatMessage("twitch", "chan", "Alice", "still here", event="sub", event_value=12, event_text="Tier 1")
    small = ChatMessage("twitch", "chan", "Bob", "", event="bits", event_value=5)
    raid = ChatMessage("twitch", "chan", "Carol", "", event="raid", event_value=42)
    items = w.view.items([sub, small, raid])
    assert items[0][2] and "resubscribed (12 months)" in items[0][1]
    assert items[1][2] is None and "cheered 5 bits" in items[1][1]  # below the minimum: no highlight
    assert items[2][2] and f"msg:{raid.uid}" in items[2][1]  # name stays clickable
    w.view.rebuild(items)
    assert "Carol is raiding with 42 viewers" in w.view.toPlainText()
    p = message_payload(sub, w.view.alerts_cfg)
    assert p["alert"] and p["event"] == "sub" and p["headline"].startswith("Alice resubscribed")
    assert message_payload(small, w.view.alerts_cfg)["alert"] is False

    dlg = dialogs.AlertsDialog(w, w.config["alerts"])
    dlg.checks["raid"].setChecked(False)
    dlg.mins["min_bits"].setValue(500)
    values = dlg.values()
    assert values["raid"] is False and values["min_bits"] == 500 and values["sub"] is True


def test_deletion_updates_single_lines(window):
    w = window
    msgs = [ChatMessage("twitch", "chan", f"u{i}", f"text {i}", msg_id=f"m{i}", user_id=str(i)) for i in range(300)]
    msgs.append(ChatMessage("twitch", "chan", "Zed", "", event="raid", event_value=9))
    for m in msgs:
        w.add_message(m)
    w._flush_messages()
    w._render_now()
    blocks = w.view.document().blockCount()
    called = []
    w.render_all = lambda: called.append(1)  # must not be needed
    w.apply_deletion(Deletion("twitch", msg_id="m150"))
    assert not called and w.view.document().blockCount() == blocks
    text = w.view.toPlainText()
    assert "text 150 (deleted)" in text and "text 149" in text and "text 151 (deleted)" not in text
    # the alert line keeps its highlight when it changes
    raid = msgs[-1]
    w.view.update_messages(w.view.items([raid]))
    assert w.view.document().lastBlock().blockFormat().background().color().name() != "#000000"


def test_watchdog_reports_stalls(window, caplog):
    import logging
    import time as _time

    w = window
    stats = w._perf_stats()
    assert {"messages/s", "chat lines", "animations playing"} <= stats.keys()
    wd = w.watchdog
    with caplog.at_level(logging.DEBUG, logger="multichat.perf"):
        wd._last = _time.monotonic() - 0.6  # the event loop was blocked for ~500 ms
        wd._tick()
        wd._summary_at = 0
        wd._tick()
    text = caplog.text
    assert "Window was busy for" in text and "Last minute: 1 stalls" in text


def test_about_dialog_fits_its_text(window, app):
    from PyQt6.QtGui import QFont

    from multichat.ui.dialogs import AboutDialog

    old = app.font()
    try:
        big = QFont(old)
        big.setPointSizeF(18)
        app.setFont(big)
        dlg = AboutDialog(window)
        dlg.show()
        app.processEvents()
        assert dlg.height() >= dlg.layout().totalHeightForWidth(dlg.width()) - 1  # nothing cut off
        dlg.close()
    finally:
        app.setFont(old)


def test_changed_channel_stops_its_old_chat(window, app):
    w = window
    row = w.panels[0].rows["kick"]
    row.edit.setText("examplekick")
    w.toggle_row(row)
    assert "kick:examplekick" in w.readers
    row.edit.setText("otherkick")  # the old channel's chat must not keep running unseen
    assert "kick:examplekick" not in w.readers
    stray = ChatMessage("kick", "examplekick", "User", "late message")
    stray.source = "kick:examplekick"
    assert w.panels_for(stray) == []  # a late message of it goes nowhere (not into group 1)
    row.edit.setText("")


def test_mention_counter_matches_whole_names(window, app):
    w = window
    w.accounts["twitch"].store.update("twitch", access_token="x", expires_at=9e12, user_name="Me")
    second = w.add_group()
    second.rows["twitch"].edit.setText("examplechan")
    w.tabs.setCurrentIndex(0)
    for text in ("hi @meow", "hey @Me!", "@me"):
        m = ChatMessage("twitch", "examplechan", "User", text)
        m.source = "twitch:examplechan"
        w.add_message(m)
    w._flush_messages()
    assert second.unread == 3 and second.mentions == 2
    w.logout("twitch")


def test_video_settings_stay_bounded():
    from multichat.core import config

    many = {f"twitch:chan{i}": "720p" for i in range(config.MAX_REMEMBERED + 50)}
    kept = config._newest(many)
    assert len(kept) == config.MAX_REMEMBERED and "twitch:chan149" in kept and "twitch:chan0" not in kept


def test_busy_group_does_not_push_out_a_quiet_one(window, app, monkeypatch):
    from multichat.ui import main_window as mw

    monkeypatch.setattr(mw, "MAX_MESSAGES", 50)
    monkeypatch.setattr(mw, "TRIM_STEP", 10)
    w = window
    w._trim_at = 50
    w.panels[0].rows["twitch"].edit.setText("quietchan")
    busy = w.add_group()
    busy.rows["twitch"].edit.setText("busychan")

    def msg(chan, text):
        m = ChatMessage("twitch", chan, "User", text)
        m.source = f"twitch:{chan}"
        w.add_message(m)

    for i in range(30):
        msg("quietchan", f"quiet {i}")
    for i in range(400):
        msg("busychan", f"busy {i}")
        if i % 25 == 0:
            w._flush_messages()
    w._flush_messages()
    quiet = [m for m in w.messages.values() if m.channel == "quietchan"]
    noisy = [m for m in w.messages.values() if m.channel == "busychan"]
    assert len(quiet) == 30  # all kept – only the busy group is trimmed
    assert 50 <= len(noisy) <= 60 and noisy[-1].text == "busy 399"


def test_overlay_payloads_carry_source_and_groups(window):
    from multichat.chat.models import Deletion as D
    from multichat.overlay.server import deletion_payload, message_payload

    m = ChatMessage("twitch", "examplechan", "User", "hi")
    m.source = "twitch:examplechan"
    p = message_payload(m, None, [2])
    assert p["source"] == "twitch:examplechan" and p["groups"] == [2]
    d = deletion_payload(D("twitch", user_id="7", source="twitch:examplechan"))
    assert d["source"] == "twitch:examplechan" and d["user_id"] == "7"
    assert window.overlay_url(2).endswith("&group=2") and "group" not in window.overlay_url()
