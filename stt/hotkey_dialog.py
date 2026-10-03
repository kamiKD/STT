"""Modal dialog that captures a key combo from the keyboard.

Opened from the tray menu so the hotkey can be rebound without editing
config.json or restarting. The keyboard is grabbed while the dialog is up, so
the combo lands here instead of in the window behind it.

The app's own low-level hook still sees those keystrokes, so the caller is
expected to ignore hotkey events for the duration (`App._capturing`).

Visually it is the same material as the status card - one style sheet, one type
scale, one accent dict - because a rebind dialog that looks like a different
application is jarring every time it opens. It is frameless, so the body drags
it and there is no title bar fighting the composition.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QKeyEvent, QPainter
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout

from . import keys as keyutil
from . import material
from .overlay import ACCENT_ERROR, ACCENT_OK
from .widgets import ElidedLabel, paint_shadow

# Modifier flags, in canonical order, mapped to the tokens keys.parse_combo takes.
MODIFIER_FLAGS = (
    (Qt.KeyboardModifier.ControlModifier, "ctrl"),
    (Qt.KeyboardModifier.AltModifier, "alt"),
    (Qt.KeyboardModifier.ShiftModifier, "shift"),
    (Qt.KeyboardModifier.MetaModifier, "win"),
)

POP = (280, QEasingCurve.Type.OutBack)

# The sheet owns its corners outright, and the shadow has to live inside the
# window or the blur gets clipped and leaves a hard rim. See stt.overlay.
CORNER_RADIUS = 22
SHADOW_SPREAD = 24


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


def _stylesheet() -> str:
    """Near-opaque black sheet over a truly transparent window: only the sheet shows."""
    face = material.font_family()
    return "\n".join(
        [
            "QWidget { background: transparent; }",
            "QDialog#hotkey { background: transparent; }",
            "QFrame#sheet {",
            f"  background-color: {material.PILL_FILL};",
            "  border: none;",
            f"  border-radius: {CORNER_RADIUS}px;",
            "}",
            "QLabel { background: transparent; border: none; }",
            f'QLabel#head {{ font-family: "{face}"; font-size: 15px; '
            "font-weight: 600; color: #f6f6fa; }",
            f'QLabel#caption {{ font-family: "{face}"; font-size: 13px; '
            "font-weight: 400; color: #c2c3cc; }",
            f'QLabel#preview {{ font-family: "{face}"; font-size: 34px; '
            "font-weight: 600; color: #f6f6fa; }",
            f'QLabel#status {{ font-family: "{face}"; font-size: 13px; '
            "font-weight: 400; color: #c2c3cc; }",
            f'QLabel#status[level="warn"] {{ color: {ACCENT_ERROR}; }}',
            f'QLabel#status[level="ok"] {{ color: {ACCENT_OK}; }}',
        ]
    )


class HotkeyDialog(QDialog):
    """Ask for a combo by pressing it. Esc cancels, closing without saving."""

    def __init__(self, title: str, hint: str, current=None, parent=None):
        super().__init__(parent)
        self.setObjectName("hotkey")
        self.setWindowTitle(title)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        # The window itself paints nothing so only the sheet is visible.
        self.setAutoFillBackground(False)
        self.setModal(True)
        self.setFixedWidth(430)

        self._combo: list[str] | None = None
        self._held: set[str] = set()
        self._drag_from: QPoint | None = None
        self.setStyleSheet(_stylesheet())

        from PySide6.QtWidgets import QFrame

        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            SHADOW_SPREAD, SHADOW_SPREAD, SHADOW_SPREAD, SHADOW_SPREAD
        )
        self._sheet = QFrame(self)
        self._sheet.setObjectName("sheet")
        outer.addWidget(self._sheet)

        layout = QVBoxLayout(self._sheet)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(0)

        head = QLabel(hint, self._sheet)
        head.setObjectName("head")
        head.setWordWrap(True)
        layout.addWidget(head)

        if current:
            caption = QLabel(f"Currently {keyutil.combo_label(current)}", self._sheet)
            caption.setObjectName("caption")
            layout.addSpacing(6)
            layout.addWidget(caption)

        layout.addSpacing(18)

        self._preview = QLabel("", self._sheet)
        self._preview.setObjectName("preview")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._preview)

        layout.addSpacing(12)

        self._status = ElidedLabel(
            "Hold Ctrl, Alt, Shift or Win together with one key. Esc cancels.",
            max_lines=2,
            parent=self._sheet,
        )
        self._status.setObjectName("status")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        self._preview.setText(
            keyutil.combo_label(current) if current else "press a combo"
        )

        # The preview swells briefly when a combo lands: the confirmation that
        # the press registered before the window closes.
        self._pop = QPropertyAnimation(self._preview, b"maximumHeight", self)
        self._pop.setDuration(0 if material.reduced_motion() else POP[0])
        self._pop.setEasingCurve(POP[1])
        self._pop.finished.connect(self._settle_pop)

    # ------------------------------------------------------------------ state
    def combo(self) -> list[str] | None:
        """The captured combo, or None when the dialog was cancelled."""
        return self._combo

    def _held_label(self) -> str:
        order = {token: index for index, (_, token) in enumerate(MODIFIER_FLAGS)}
        return keyutil.combo_label(sorted(self._held, key=lambda t: order[t]))

    def _set_status(self, text: str, level: str = "") -> None:
        self._status.set_text(text)
        self._status.setProperty("level", level)
        style = self.style()
        style.unpolish(self._status)
        style.polish(self._status)

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

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:
        """The shadow only; the sheet itself is pure style sheet."""
        paint_shadow(
            QPainter(self),
            QRectF(SHADOW_SPREAD, SHADOW_SPREAD, self.width() - SHADOW_SPREAD * 2,
                   self.height() - SHADOW_SPREAD * 2),
            CORNER_RADIUS,
            spread=SHADOW_SPREAD,
        )

    # ------------------------------------------------------------------ pop
    def _play_pop(self) -> None:
        """Animate the preview taller and let OutBack overshoot, then settle.

        Animating maximumHeight rather than a scale keeps the surrounding layout
        still: a scaled label would shift the dialog's contents on every frame.
        """
        natural = self._preview.sizeHint().height()
        self._preview.setMaximumHeight(natural)
        self._pop.stop()
        self._pop.setStartValue(self._preview.maximumHeight())
        self._pop.setEndValue(int(natural * 1.18))
        self._pop.start()

    def _settle_pop(self) -> None:
        self._preview.setMaximumHeight(self._preview.sizeHint().height())

    # ----------------------------------------------------------------- events
    def showEvent(self, event) -> None:
        super().showEvent(event)
        # After the platform window exists, so events are actually delivered.
        self.grabKeyboard()
        if not material.reduced_motion():
            self.setWindowOpacity(0.0)
            self._fade_in = QPropertyAnimation(self, b"windowOpacity", self)
            self._fade_in.setDuration(150)
            self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._fade_in.setStartValue(0.0)
            self._fade_in.setEndValue(1.0)
            self._fade_in.start()

    def done(self, result: int) -> None:
        self.releaseKeyboard()
        super().done(result)

    def mousePressEvent(self, event) -> None:
        # Frameless means nothing to drag by, so the body does it.
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_from = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None:
            self.move(event.globalPosition().toPoint() - self._drag_from)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(event)

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
            self._set_status(
                "That key cannot be used here. Try a letter, a digit or F1-F12.",
                level="warn",
            )
            return
        if not modifiers:
            self._set_status(
                "Hold Ctrl, Alt, Shift or Win as well - a bare key would fire "
                "while you type.",
                level="warn",
            )
            self._preview.setText(token.upper())
            return

        try:
            combo = keyutil.parse_combo("+".join(modifiers) + "+" + token)
        except ValueError as exc:
            self._set_status(str(exc), level="warn")
            return

        self._combo = combo
        self._preview.setText(keyutil.combo_label(combo))
        self._set_status("Bound. Release to apply.", level="ok")
        self._play_pop()
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