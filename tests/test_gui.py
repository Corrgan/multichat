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
from multichat.core.config import load_config
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

    for name in ("run_twitch", "run_kick", "run_youtube"):
        monkeypatch.setattr(main_window, name, idle)
    cfg = load_config()
    cfg["video"]["enabled"] = False
    cfg["extra_emotes"] = False
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
    bar = w.action_bar
    assert not bar.card.isEnabled()  # nothing selected yet
    w.on_anchor(QUrl(f"msg:{first.uid}"))  # left click selects
    assert w.mod.selected is first and bar.card.isEnabled()
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
    w.open_card(second)
    settle()
    card = w.card
    assert card.isVisible() and card.info_labels["seen"].text() == "2"
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


def test_connect_disconnect(window, app):
    w = window
    w.inputs["twitch"].setText("examplechan")
    w.inputs["kick"].setText("")
    w.inputs["youtube"].setText("")
    w.connect_all()
    for _ in range(50):
        app.processEvents()
        if w.status_labels["twitch"].full.startswith("connected"):
            break
        asyncio.run(asyncio.sleep(0.02))
    assert w.status_labels["twitch"].full == "connected (#examplechan)"
    w.disconnect_all()
    app.processEvents()
    assert w.readers == {} and w.status_labels["twitch"].full == "disconnected"


def test_single_platform_connect_disconnect(window, app):
    w = window
    w.inputs["twitch"].setText("examplechan")
    w.inputs["kick"].setText("examplekick")
    w.inputs["youtube"].setText("")
    w.connect_all()
    assert set(w.readers) == {"twitch", "kick"}
    assert w.row_buttons["youtube"].isEnabled() is False  # nothing entered
    w.toggle_platform("kick")  # disconnect only Kick
    app.processEvents()
    assert set(w.readers) == {"twitch"} and w.status_labels["kick"].full == "disconnected"
    assert w.connect_btn.text() == "Reconnect" and w.disconnect_btn.isEnabled()
    old_gen = w.conn_gen["kick"]
    w.toggle_platform("kick")  # and back
    assert set(w.readers) == {"twitch", "kick"} and w.conn_gen["kick"] == old_gen + 1
    w.disconnect_all()
    assert w.readers == {} and not w.disconnect_btn.isEnabled()


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
    monkeypatch.setattr(window.video, "set_sources", lambda values: started.append(values))
    window.inputs["kick"].setText("examplekick")
    window.connect_platform("kick")
    window.disconnect_platform("kick")
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
