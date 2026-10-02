"""Regression test: _set_caption_edit_enabled must only touch widgets that exist.

Root cause (2026-10-02, Uzair's PC log):
    Caption editor unavailable: 'MainWindow' object has no attribute 'edit_compose_btn'

The post-render "Edit Captions" panel builder (_captions_tab) never created
self.edit_compose_btn (the Compose feature was removed), but
_set_caption_edit_enabled() still referenced it. On a successful render,
_use_edit_session() called _set_caption_edit_enabled(True) -> AttributeError,
which was caught and misread as a session failure; the failure path then
called _set_caption_edit_enabled(False) -> AttributeError AGAIN, this time
uncaught, aborting _on_finished() mid-way. Result: caption controls stayed
disabled AND the preview never loaded the rendered video (all the
play-diag NoMedia watchdog-gave-up lines).

This test statically guarantees every self.<widget> touched by
_set_caption_edit_enabled is assigned somewhere in MainWindow, so a removed
widget can never again silently kill the post-render finish sequence.
"""
import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "ui" / "main_window.py"


def _method_node(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return item
    raise AssertionError(f"MainWindow.{name} not found")


def _self_attr_loads(fn):
    return {
        n.attr
        for n in ast.walk(fn)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
    }


def _self_attr_stores(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
            for n in ast.walk(node):
                if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) \
                        and n.value.id == "self" and isinstance(n.ctx, ast.Store):
                    found.add(n.attr)
    return found


def test_caption_edit_toggle_only_touches_existing_widgets():
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    toggle = _method_node(tree, "_set_caption_edit_enabled")
    touched = _self_attr_loads(toggle)
    assert touched, "_set_caption_edit_enabled touches no widgets?"
    existing = _self_attr_stores(tree)
    missing = sorted(touched - existing)
    assert not missing, (
        "Stale widget reference(s) in _set_caption_edit_enabled would raise "
        f"AttributeError and abort _on_finished(): {missing}"
    )


def test_use_edit_session_failure_path_cannot_raise_attribute_error():
    """The not-ok path in _use_edit_session calls _set_caption_edit_enabled(False);
    combined with the test above, this guarantees no AttributeError can escape
    _on_finished() from the caption-edit cluster."""
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = _method_node(tree, "_use_edit_session")
    src = ast.get_source_segment(SRC.read_text(encoding="utf-8"), fn)
    assert "_set_caption_edit_enabled(False)" in src
    # And _on_finished must call _use_edit_session (the wiring that crashed).
    fin = _method_node(tree, "_on_finished")
    fin_src = ast.get_source_segment(SRC.read_text(encoding="utf-8"), fin)
    assert "_use_edit_session(" in fin_src
