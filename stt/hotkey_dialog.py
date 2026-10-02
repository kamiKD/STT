"""Modal dialog that captures a key combo from the keyboard.

Opened from the tray menu so the hotkey can be rebound without editing
config.json or restarting. The keyboard is grabbed while the dialog is up, so
the combo lands here instead of in the window behind it.

The app's own low-level hook still sees those keystrokes, so the caller is
expected to ignore hotkey events for the duration (`App._capturing`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from . import keys as keyutil

# Modifier flags, in canonical order, mapped to the tokens keys.parse_combo takes.
MODIFIER_FLAGS = (
    (Qt.KeyboardModifier.ControlModifier, "ctrl"),
    (Qt.KeyboardModifier.AltModifier, "alt"),
    (Qt.KeyboardModifier.ShiftModifier, "shift"),
    (Qt.KeyboardModifier.MetaModifier, "win"),
)

def _as_int(value) -> int:
    """PySide6 hands back enum.Flag members here, plain ints on older builds."""
    return int(getattr(value, "value", value))


MODIFIER_NAME = {
    _as_int(Qt.Key.Key_Control): "ctrl",
    _as_int(Qt.Key.Key_Alt): "alt",
    _as_int(Qt.Key.Key_Shift): "shift",
    _as_int(Qt.Key.Key_Meta): "win",
}
MODIFIER_KEYS = set(MODIFIER_NAME)

NAMED_KEYS = {
    "Key_Space": "space",
    "Key_Return": "enter",
    "Key_Enter": "enter",
    "Key_Tab": "tab",
    "Key_Backspace": "backspace",
    "Key_Escape": "esc",
    "Key_Insert": "insert",
    "Key_Pause": "pause",
    "Key_ScrollLock": "scroll_lock",
    "Key_CapsLock": "caps_lock",
    "Key_QuoteLeft": "`",
}


def _build_key_table() -> dict[int, str]:
    """Qt key code -> combo token.

    Enumerators are read by name and keyed by int, because QKeyEvent.key()
    returns a plain int on some PySide6 builds and an enum member on others.
    """
    table: dict[int, str] = {}
    for name, token in NAMED_KEYS.items():
        code = getattr(Qt.Key, name, None)
        if code is not None:
            table[_as_int(code)] = token
    for i in range(1, 25):
        code = getattr(Qt.Key, f"Key_F{i}", None)
        if code is not None:
            table[_as_int(code)] = f"f{i}"
    for letter in "abcdefghijklmnopqrstuvwxyz":
        code = getattr(Qt.Key, f"Key_{letter.upper()}", None)
        if code is not None:
            table[_as_int(code)] = letter
    for digit in "0123456789":
        code = getattr(Qt.Key, f"Key_{digit}", None)
        if code is not None:
            table[_as_int(code)] = digit
    return table


KEY_TABLE = _build_key_table()


def _modifier_tokens(modifiers) -> list[str]:
    value = _as_int(modifiers)
    return [token for flag, token in MODIFIER_FLAGS if value & _as_int(flag)]


class HotkeyDialog(QDialog):
    """Ask for a combo by pressing it. Esc cancels, closing without saving."""

    def __init__(self, title: str, hint: str, current=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setModal(True)
        self.setMinimumWidth(400)

        self._combo: list[str] | None = None
        self._held: set[str] = set()

        layout = QVBoxLayout(self)

        head = QLabel(hint, self)
        head.setWordWrap(True)
        layout.addWidget(head)

        self._preview = QLabel("", self)
        font = self._preview.font()
        font.setPointSize(max(14, font.pointSize() + 4))
        font.setBold(True)
        self._preview.setFont(font)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._preview)

        if current:
            now = QLabel(f"Currently: {keyutil.combo_label(current)}", self)
            now.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(now)

        self._status = QLabel(
            "Hold Ctrl, Alt, Shift or Win together with one key. Esc cancels.",
            self,
        )
        self._status.setWordWrap(True)
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        self._preview.setText(keyutil.combo_label(current) if current else "press a combo")

    # ------------------------------------------------------------------ state
    def combo(self) -> list[str] | None:
        """The captured combo, or None when the dialog was cancelled."""
        return self._combo

    def _held_label(self) -> str:
        order = {token: index for index, (_, token) in enumerate(MODIFIER_FLAGS)}
        return keyutil.combo_label(sorted(self._held, key=lambda t: order[t]))

    def _modifiers_in_effect(self, event: QKeyEvent) -> list[str]:
        """Modifiers for this event, merged with the ones we tracked ourselves.

        QKeyEvent.modifiers() deliberately excludes the modifier key that the
        event is about, so a live "Ctrl + ..." preview needs its own state.
        """
        tokens = _modifier_tokens(event.modifiers())
        for token in self._held:
            if token not in tokens:
                tokens.append(token)
        order = {token: index for index, (_, token) in enumerate(MODIFIER_FLAGS)}
        tokens.sort(key=lambda token: order[token])
        return tokens

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # After the platform window exists, so events are actually delivered.
        self.grabKeyboard()

    def done(self, result: int) -> None:
        self.releaseKeyboard()
        super().done(result)

    # ----------------------------------------------------------------- events
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = _as_int(event.key())
        if key == _as_int(Qt.Key.Key_Escape):
            self.reject()
            return

        if key in MODIFIER_KEYS:
            # Still assembling the combo: show what has been held down so far.
            self._held.add(MODIFIER_NAME[key])
            self._preview.setText(self._held_label() + " + ...")
            return

        modifiers = self._modifiers_in_effect(event)
        token = KEY_TABLE.get(key, "")
        if not token:
            self._status.setText("That key cannot be used here. Try a letter, a digit or F1-F12.")
            return
        if not modifiers:
            self._status.setText(
                "Hold Ctrl, Alt, Shift or Win as well - "
                "a bare key would fire while you type."
            )
            self._preview.setText(token.upper())
            return

        try:
            combo = keyutil.parse_combo("+".join(modifiers) + "+" + token)
        except ValueError as exc:
            self._status.setText(str(exc))
            return

        self._combo = combo
        self.accept()

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        key = _as_int(event.key())
        token = MODIFIER_NAME.get(key)
        if token is not None:
            self._held.discard(token)
            if not self._held:
                self._preview.setText("press a combo")
            else:
                self._preview.setText(self._held_label() + " + ...")
            return
        super().keyReleaseEvent(event)