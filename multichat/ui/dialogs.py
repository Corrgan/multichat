# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""Dialogs: developer apps and About."""
from __future__ import annotations

import platform as py_platform

from PyQt6.QtCore import QT_VERSION_STR, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget,
)

from .. import __version__ as APP_VERSION
from ..accounts.oauth import REDIRECT_URI
from ..core.constants import LOGIN_DOCS, NAMES, PLATFORMS, REPO_URL
from ..core.i18n import _
from ..core.paths import app_icon_path


DEV_CONSOLES = {
    "twitch": "https://dev.twitch.tv/console/apps",
    "kick": "https://kick.com/settings/developer",
    "youtube": "https://console.cloud.google.com/apis/credentials",
}


class AppsDialog(QDialog):
    """Enter client ID and secret of the user's own developer apps."""

    def __init__(self, parent: QWidget, apps: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("Developer apps"))
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        intro = QLabel(
            _(
                "To log in you need your own free developer app per platform. Enter exactly "
                "<b>{redirect}</b> as its redirect URL and copy the client ID and secret here."
            ).format(redirect=REDIRECT_URI)
            + f" <a href='{LOGIN_DOCS}'>{_('Step-by-step guide with all options')}</a>"
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        lay.addWidget(intro)
        self.fields: dict[str, tuple[QLineEdit, QLineEdit]] = {}
        for p in PLATFORMS:
            box = QGroupBox(NAMES[p])
            form = QFormLayout(box)
            link = QLabel(f"<a href='{DEV_CONSOLES[p]}'>{DEV_CONSOLES[p]}</a>")
            link.setOpenExternalLinks(True)
            cid = QLineEdit(apps.get(p, {}).get("client_id", ""))
            sec = QLineEdit(apps.get(p, {}).get("client_secret", ""))
            sec.setEchoMode(QLineEdit.EchoMode.Password)
            form.addRow(_("Console:"), link)
            form.addRow(_("Client ID:"), cid)
            form.addRow(_("Client secret:"), sec)
            self.fields[p] = (cid, sec)
            lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def values(self) -> dict:
        return {p: {"client_id": c.text().strip(), "client_secret": s.text().strip()} for p, (c, s) in self.fields.items()}


class AboutDialog(QDialog):
    """Help → About MultiChat."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("About MultiChat"))
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        icon = QLabel()
        pix = QPixmap(str(app_icon_path()))
        if not pix.isNull():
            icon.setPixmap(pix.scaled(72, 72, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        head.addWidget(icon)
        title = QLabel(
            f"<h2 style='margin:0'>MultiChat</h2>"
            f"<div>{_('Version {version}').format(version=APP_VERSION)}</div>"
            f"<div style='color:#888'>{_('Twitch, Kick and YouTube chat in one window')}</div>"
        )
        head.addWidget(title, 1)
        lay.addLayout(head)
        license_link = f"<a href='{REPO_URL}/blob/main/LICENSE'>GNU GPL v3.0 or later</a>"
        notices_link = f"<a href='{REPO_URL}/blob/main/THIRD_PARTY_NOTICES.md'>THIRD_PARTY_NOTICES</a>"
        repo_text = REPO_URL.removeprefix("https://")
        body = QLabel(
            f"<p><a href='{REPO_URL}'>{repo_text}</a></p>"
            "<p>" + _("Free software under the {license}.").format(license=license_link) + "<br>"
            + _("Uses third-party libraries under their own licenses – see {notices}.").format(notices=notices_link)
            + "</p>"
            "<p>" + _("Not affiliated with Twitch, Kick, YouTube/Google, 7TV, BetterTTV or FrankerFaceZ.") + "</p>"
            f"<p style='color:#888'>Qt {QT_VERSION_STR} · Python {py_platform.python_version()} · "
            f"{QApplication.platformName()}</p>"
        )
        body.setWordWrap(True)
        body.setOpenExternalLinks(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        lay.addWidget(body)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
