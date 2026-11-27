"""Multidioma de la ventana: todos los textos marcados con tr() tienen traducción al inglés."""

import ast
from pathlib import Path

from rkbx_practice.ui.i18n import EN, set_language, tr

UI = Path(__file__).resolve().parent.parent / "src" / "rkbx_practice" / "ui"


def _tr_literals():
    for path in sorted(UI.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args:
                a = node.args[0]
                options = [a] if isinstance(a, ast.Constant) else (
                    [a.body, a.orelse] if isinstance(a, ast.IfExp) else [])
                for o in options:
                    if isinstance(o, ast.Constant) and isinstance(o.value, str):
                        yield path.name, o.value


def test_every_ui_text_has_english_translation():
    missing = [(f, t) for f, t in _tr_literals() if t not in EN]
    assert not missing, missing
    assert len(list(_tr_literals())) > 80


def test_tr_switches_language_and_formats():
    try:
        set_language("en")
        assert tr("Guardar") == "Save"
        assert tr("Tira {n}", n=3) == "Strip 3"
        assert tr("texto sin traducir") == "texto sin traducir"
        set_language("xx")                       # desconocido → español
        assert tr("Guardar") == "Guardar"
        assert tr("Tira {n}", n=2) == "Tira 2"
    finally:
        set_language("es")


def test_translations_keep_placeholders():
    import string
    fmt = string.Formatter()
    for es, en in EN.items():
        names_es = {f for _, f, _, _ in fmt.parse(es) if f}
        names_en = {f for _, f, _, _ in fmt.parse(en) if f}
        assert names_es == names_en, es
