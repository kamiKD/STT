"""Tests for the hotkey capture dialog.

Key events are synthesized: QTest would need a real window handle, and
`keyPressEvent` is the whole surface here.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog

from stt import material
from stt.hotkey_dialog import HotkeyDialog

pytest.importorskip("pytestqt")


def press(dialog, key, modifiers=Qt.KeyboardModifier.NoModifier):
    event = QKeyEvent(QKeyEvent.Type.KeyPress, int(key), modifiers)
    dialog.keyPressEvent(event)


def release(dialog, key):
    event = QKeyEvent(QKeyEvent.Type.KeyRelease, int(key), Qt.KeyboardModifier.NoModifier)
    dialog.keyReleaseEvent(event)


CTRL_ALT = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier


def test_dialog_captures_a_modifier_plus_key(qtbot):
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    press(dialog, Qt.Key.Key_O, CTRL_ALT)
    assert dialog.combo() == ["ctrl", "alt", "o"]
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_dialog_canonicalizes_modifier_order(qtbot):
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    press(
        dialog,
        Qt.Key.Key_M,
        Qt.KeyboardModifier.ShiftModifier
        | Qt.KeyboardModifier.ControlModifier,
    )
    assert dialog.combo() == ["ctrl", "shift", "m"]


@pytest.mark.parametrize(
    "key,expected",
    [
        (Qt.Key.Key_Space, "space"),
        (Qt.Key.Key_F5, "f5"),
        (Qt.Key.Key_7, "7"),
    ],
)
def test_dialog_maps_special_keys(qtbot, key, expected):
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    press(dialog, key, CTRL_ALT)
    assert dialog.combo() == ["ctrl", "alt", expected]


def test_dialog_rejects_a_bare_key(qtbot):
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    press(dialog, Qt.Key.Key_O)
    assert dialog.combo() is None
    assert dialog.result() == 0


def test_dialog_rejects_an_unusable_key(qtbot):
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    press(dialog, Qt.Key.Key_Print, CTRL_ALT)
    assert dialog.combo() is None


def test_dialog_escape_cancels(qtbot):
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    press(dialog, Qt.Key.Key_Escape)
    assert dialog.combo() is None
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_dialog_shows_the_current_binding(qtbot):
    dialog = HotkeyDialog("Hotkey", "Press it", ["ctrl", "alt", "space"])
    qtbot.addWidget(dialog)
    assert "Ctrl+Alt+Space" in dialog._preview.text()


def test_dialog_fades_in(qtbot):
    """The dialog must appear with a fade, not pop into existence."""
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.windowOpacity() < 1.0
    loop = QEventLoop()
    QTimer.singleShot(400, loop.quit)
    loop.exec()
    assert dialog.windowOpacity() == pytest.approx(1.0, abs=1e-6)


def test_dialog_fade_in_is_skipped_with_reduced_motion(qtbot, monkeypatch):
    monkeypatch.setattr(material, "reduced_motion", lambda: True)
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    dialog.show()
    assert dialog.windowOpacity() == pytest.approx(1.0, abs=1e-6)


def test_dialog_tracks_modifiers_pressed_one_by_one(qtbot):
    """Real dialogs see Ctrl then Alt then O, each event carrying no modifiers."""
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    press(dialog, Qt.Key.Key_Control, Qt.KeyboardModifier.NoModifier)
    press(dialog, Qt.Key.Key_Alt, Qt.KeyboardModifier.NoModifier)
    press(dialog, Qt.Key.Key_O, Qt.KeyboardModifier.NoModifier)
    assert dialog.combo() == ["ctrl", "alt", "o"]


def test_modifier_press_only_updates_the_preview(qtbot):
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    # Qt hides the modifier the event is about, so the dialog tracks it itself.
    press(dialog, Qt.Key.Key_Control, Qt.KeyboardModifier.NoModifier)
    assert dialog.combo() is None
    assert "Ctrl" in dialog._preview.text()


def test_releasing_every_modifier_resets_the_preview(qtbot):
    dialog = HotkeyDialog("Hotkey", "Press it")
    qtbot.addWidget(dialog)
    press(dialog, Qt.Key.Key_Control, Qt.KeyboardModifier.NoModifier)
    release(dialog, Qt.Key.Key_Control)
    assert dialog._preview.text() == "press a combo"
    assert dialog.combo() is None