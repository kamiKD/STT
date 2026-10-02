"""Floating recording overlay: level meter, state text, and last transcript.

Frameless, always-on-top, click-through-ish (it does not accept focus, so it
never steals it from the app the user is typing into).
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

BG = QColor(18, 18, 22, 235)
BORDER = QColor(255, 255, 255, 28)
TEXT = QColor(240, 240, 245)
MUTED = QColor(150, 152, 165)
IDLE_BAR = QColor(90, 92, 105)
REC_BAR = QColor(255, 92, 92)
BUSY_BAR = QColor(120, 170, 255)
OK_BAR = QColor(90, 210, 150)
WARN_BAR = QColor(255, 190, 90)

BARS = 28


class RecordingOverlay(QWidget):
    """Compact status bar; auto-hides when idle."""

    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedSize(420, 96)

        self._state = "idle"
        self._level = 0.0
        self._message = ""
        self._transcript = ""
        self._phase = 0

        self._font_main = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
        self._font_sub = QFont("Segoe UI", 9)
        self._font_text = QFont("Segoe UI", 10)

        self._level = 0.0
        self._smooth = 0.0
        self._phase = 0

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        # Owned timer instead of QTimer.singleShot so a pending hide can be
        # cancelled when new state arrives.
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(lambda: self.set_state("idle"))

        self._reposition()
        self.hide()

    # ------------------------------------------------------------------ state
    def is_alive(self) -> bool:
        """False once the underlying C++ object is gone.

        Pending singleShot callbacks can outlive the widget, and touching a
        deleted QWidget aborts the process rather than raising.
        """
        try:
            self.objectName()
            return True
        except RuntimeError:
            return False

    def schedule_hide(self, ms: int = 3500) -> None:
        """Return to idle after `ms`, unless new state arrives first."""
        if not self.is_alive():
            return
        self._hide_timer.start(ms)

    def set_state(self, state: str, message: str = "") -> None:
        """state: idle | recording | transcribing | done | error"""
        if not self.is_alive():
            return
        if state == "idle":
            self._hide_timer.stop()
        else:
            self._hide_timer.stop()  # a new message supersedes any pending hide
        self._state = state
        self._message = message
        if state == "idle":
            self._reposition()
            self.hide()
        else:
            if not self.isVisible():
                self._reposition()
                self.show()
            self.update()

    def set_transcript(self, text: str) -> None:
        if not self.is_alive():
            return
        self._transcript = text
        self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))

    def current_state(self) -> str:
        return self._state

    # ----------------------------------------------------------------- render
    def _tick(self) -> None:
        if self._state == "idle" or not self.is_alive():
            return
        self._phase += 0.35
        if self._state == "recording":
            self._smooth += (self._level - self._smooth) * 0.35
        else:
            target = 0.25 if self._state == "transcribing" else 0.15
            self._smooth += (target - self._smooth) * 0.15
        self.update()

    def _bar_color(self) -> QColor:
        return {
            "recording": REC_BAR,
            "transcribing": BUSY_BAR,
            "done": OK_BAR,
            "error": WARN_BAR,
        }.get(self._state, IDLE_BAR)

    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:  # pragma: no cover - headless
            return
        area = screen.availableGeometry()
        x = area.center().x() - self.width() // 2
        y = area.bottom() - self.height() - 48
        self.move(x, y)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0.5, 0.5, self.width() - 1, self.height() - 1, 16, 16)
        p.fillPath(path, BG)
        p.setPen(QPen(BORDER, 1))
        p.drawPath(path)

        color = self._bar_color()

        # Level meter on the left.
        bar_w = 3
        gap = 3
        total = BARS * bar_w + (BARS - 1) * gap
        x0 = 18
        mid_y = self.height() / 2
        for i in range(BARS):
            # Symmetric shape, deterministic phase so it looks like a waveform.
            wave = (math.sin(self._phase + i * 0.45) + 1) / 2
            amp = max(0.06, self._smooth * (0.45 + 0.55 * wave))
            h = 6 + amp * (self.height() - 34)
            bx = x0 + i * (bar_w + gap)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawRoundedRect(bx, mid_y - h / 2, bar_w, h, 1.5, 1.5)

        # Text block.
        text_x = x0 + total + 20
        p.setFont(self._font_main)
        p.setPen(color if self._state in ("recording", "error") else TEXT)
        p.drawText(
            text_x,
            30,
            self.width() - text_x - 16,
            20,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._headline(),
        )

        p.setFont(self._font_sub)
        p.setPen(MUTED)
        p.drawText(
            text_x,
            50,
            self.width() - text_x - 16,
            30,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            | int(Qt.TextFlag.TextWordWrap),
            self._detail(),
        )

    def _headline(self) -> str:
        return {
            "recording": "Recording",
            "transcribing": "Transcribing",
            "done": "Done",
            "error": "Problem",
        }.get(self._state, "")

    def _detail(self) -> str:
        if self._state == "recording":
            return self._message or "Speak now, release to insert."
        if self._state == "transcribing":
            return self._message or "Sending audio to Groq..."
        if self._state == "error":
            return self._message
        if self._transcript:
            return self._transcript
        return self._message
