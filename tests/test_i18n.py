# SPDX-License-Identifier: GPL-3.0-or-later
"""Translation catalogs: complete, consistent placeholders, no stale entries."""
import ast
import json
import string
from pathlib import Path

from multichat.core import i18n

PKG = Path(__file__).resolve().parents[1] / "multichat"
IDENTITY = {"360p", "480p", "720p"}


def source_strings() -> set[str]:
    """All texts passed to _() plus the reader status templates and label tables."""
    keys = set()
    for f in PKG.rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.args:
                a = n.args[0] if n.func.id == "_" else (n.args[1] if n.func.id == "status" and len(n.args) > 1 else None)
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    keys.add(a.value)
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("QUALITIES", "TIMEOUTS") for t in n.targets):
                keys.update(el.elts[-1].value for el in n.value.elts)
    return keys - IDENTITY


def fields(text: str) -> list[str]:
    return sorted(f for _, f, _, _ in string.Formatter().parse(text) if f)


def test_catalogs():
    keys = source_strings()
    assert len(keys) > 100
    for code, _name in i18n.LANGUAGES:
        if code == "en":
            continue
        cat = json.loads(i18n.locale_file(code).read_text(encoding="utf-8"))
        assert not keys - cat.keys(), (code, "missing", sorted(keys - cat.keys())[:5])
        assert not cat.keys() - keys, (code, "stale", sorted(cat.keys() - keys)[:5])
        for k, v in cat.items():
            assert fields(v) == fields(k), (code, k, v)
            assert v.count("<b>") == k.count("<b>"), (code, k)
