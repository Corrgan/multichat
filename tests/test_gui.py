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
    alice, bob = list(w.messages.values())[:2]
    assert "<b>world</b>" in w.view.toPlainText()  # HTML in messages is shown as text
    w.apply_deletion(Deletion("twitch", user_id="9"))
    assert troll.deleted and not alice.deleted
    w.filters["kick"].setChecked(False)
    assert "Bob" not in w.view.toPlainText()
    w.filters["kick"].setChecked(True)
    assert "Bob" in w.view.toPlainText()

    shown = []
    QMenu.exec = lambda self, *a: shown.append([x.text() for x in self.actions()])
    w.on_anchor(QUrl(f"msg:{alice.uid}"))
    assert any(t.startswith("Log in to Twitch") for t in shown[0])
    w.accounts["twitch"].store.update("twitch", access_token="x", expires_at=9e12, user_name="TestUser")
    w.refresh_accounts()
    assert w.input.isEnabled() and w.target.count() == 1
    w.on_anchor(QUrl(f"msg:{alice.uid}"))
    assert "Delete message" in shown[1] and "Ban …" in shown[1]
    w.mention(bob)
    assert w.input.text().startswith("@Bob")
    w.logout("twitch")


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
    assert w.readers == [] and w.status_labels["twitch"].full == "disconnected"


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
        langmenu = next(a.menu() for a in w.menuBar().actions()[0].menu().actions() if a.menu())
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
