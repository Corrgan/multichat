# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Corrgan
"""User interface translations.

Source strings in the code are English and wrapped in ``_()``. Translations live in
``locales/<code>.json`` as ``{"English text": "translated text"}``; missing entries fall
back to English. Placeholders use ``str.format`` syntax, e.g. ``_("Connected ({channel})")``.
"""
from __future__ import annotations

import json
import logging

from .paths import RESOURCES


log = logging.getLogger("multichat.i18n")

# (code, native name) – "auto" follows the system language
LANGUAGES = [
    ("de", "Deutsch"),
    ("en", "English"),
    ("fr", "Français"),
    ("es", "Español"),
    ("ja", "日本語"),
    ("zh_CN", "简体中文"),
]
CODES = {code for code, _name in LANGUAGES}

_catalog: dict[str, str] = {}
_lang = "en"
_qt_translators: list = []


def locale_file(code: str):
    return RESOURCES / "locales" / f"{code}.json"


def system_language() -> str:
    from PyQt6.QtCore import QLocale

    name = QLocale.system().name()  # e.g. "de_DE", "zh_CN", "ja_JP"
    if name.startswith("zh"):
        return "zh_CN"
    base = name.split("_")[0]
    return base if base in CODES else "en"


def set_language(code: str | None) -> str:
    """Activate a language ("auto"/None = system language). Returns the active code."""
    global _catalog, _lang
    if not code or code == "auto" or code not in CODES:
        code = system_language()
    _catalog = {}
    if code != "en":
        path = locale_file(code)
        try:
            _catalog = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Translation %s could not be loaded: %s", path, e)
    _lang = code
    return code


def current() -> str:
    return _lang


def _(text: str) -> str:
    return _catalog.get(text) or text


def install_qt_translator(app) -> None:
    """Load Qt's own translations (buttons like OK/Cancel/Yes/No, context menus)."""
    from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator

    for t in _qt_translators:
        app.removeTranslator(t)
    _qt_translators.clear()
    if _lang == "en":
        return
    path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    tr = QTranslator(app)
    if tr.load(QLocale(_lang), "qtbase", "_", path):
        app.installTranslator(tr)
        _qt_translators.append(tr)
    else:
        log.debug("No Qt translation for %s in %s", _lang, path)
