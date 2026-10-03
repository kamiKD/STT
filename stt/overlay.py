"""The floating status pill.

A black, stadium-shaped pill that never takes focus, modelled on the Apple
reference: a cluster of softly pulsing dots on the left and a single line of
white text. One line, no badge, no timer, no progress bar - the pill says what
it is doing and gets out of the way.

* **QSS does the chrome.** Colour, radius and type live in one `_stylesheet()`.
  The pill is a stadium (`border-radius` = height / 2), so there are no corner
  radii to disagree about: no compositor clipping, no border, one owner.
* **QPropertyAnimation does the fade.** Opacity runs on `windowOpacity`, so the
  compositor fades the window.
* **Two widgets paint.** The dots (an orbit of soft white dots, Siri-style) and
  the single-line elided label. Everything else is declarative.
"""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from . import material
from .widgets import ElidedLabel, paint_shadow

# A pill, not a card: wide, short, fully rounded. ~5.8:1 reads balanced on a
# 1080p screen; the previous 6.9:1 read squashed. The radius is exactly half
# the height, so the shape is a stadium and corner ownership is a non-issue.
CARD_WIDTH = 420
CARD_HEIGHT = 72
PAD = 20
DOTS_W = 84
DOTS_H = 48
GAP = 14
CORNER_RADIUS = CARD_HEIGHT // 2
# The shadow lives inside the window so the blur is never clipped into a rim.
SHADOW_SPREAD = 24

# Motion: (duration ms, easing). Named for what they do.
FADE_IN = (200, QEasingCurve.Type.OutCubic)
FADE_OUT = (140, QEasingCurve.Type.InCubic)

# The pill stays black on the happy path; these are the only two hues in the
# UI. Anything else would be a second palette to keep in sync.
ACCENT_OK = "#30d158"
ACCENT_ERROR = "#ffb340"
DOT_TINT_DEFAULT = "#ffffff"
DOT_TINTS = {"error": ACCENT_ERROR}

STATEFUL = ("recording", "transcribing", "done", "error")


def _stylesheet() -> str:
    """The whole visual language, in one place.

    The pill is near-opaque black over a truly transparent window: no
    compositor backdrop, so nothing but the pill itself is ever visible. The
    margins around it stay invisible and only carry the painted shadow. No
    border: a hard edge is what notched the old corners.
    """
    fill = material.PILL_FILL
    face = material.font_family()
    return "\n".join(
        [
            "QWidget { background: transparent; }",
            "QFrame#card {",
            f"  background-color: {fill};",
            "  border: none;",
            f"  border-radius: {CORNER_RADIUS}px;",
            "}",
            "QLabel { background: transparent; border: none; }",
            "QLabel#main {",
            f'  font-family: "{face}";',
            "  font-size: 20px;",
            "  font-weight: 500;",
            "  color: #f5f5f7;",
            "}",
        ]
    )


class _Follower:
    """Frame-rate independent exponential follower: step is `1 - e^(-dt/tau)`."""

    __slots__ = ("value", "tau")

    def __init__(self, tau: float = 0.085):
        self.value = 0.0
        self.tau = tau

    def to(self, target: float, dt: float) -> float:
        if dt > 0.0:
            self.value += (target - self.value) * (1.0 - math.exp(-dt / self.tau))
        else:
            self.value = target
        return self.value

    def snap(self, value: float) -> None:
        self.value = value


class SiriDots(QWidget):
    """The Apple-style dot cluster: soft white dots drifting on an ellipse.

    Seven dots of varying size orbit slowly; voice energy widens the drift and
    swells the radii, so silence reads as a calm shimmer and speech as motion.
    When the pill is not listening the dots settle to a dim static cluster.
    """

    # Unit layout: (angle fraction, radius fraction). Hand-tuned, not uniform:
    # a perfect ring reads as a loading spinner, and this is not one.
    _DOTS = (
        (0.00, 1.00),
        (0.16, 0.72),
        (0.30, 1.02),
        (0.45, 0.62),
        (0.60, 0.95),
        (0.75, 0.68),
        (0.89, 1.05),
    )
    _BASE_R = 4.6
    _RX = 26.0
    _RY = 10.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(DOTS_W, DOTS_H)
        self._phase = 0.0
        self._energy = 0.0
        # How awake the cluster is: 1 while listening, eased back to 0 after.
        # Positions and brightness follow this, not the raw active flag, so the
        # dots coast to a stop instead of freezing mid-drift.
        self._motion = 0.0
        self._active = False
        self._tint = QColor(DOT_TINT_DEFAULT)

    def set_tint(self, name: str) -> None:
        self._tint = QColor(DOT_TINTS.get(name, DOT_TINT_DEFAULT))
        self.update()

    def tick(self, dt: float, energy: float, active: bool) -> bool:
        """Advance one frame. Returns True once fully settled.

        `energy` is 0..1 of voice. The phase keeps advancing while there is
        residual motion, so leaving a state coasts instead of snapping; a
        settled cluster skips its repaint entirely.
        """
        self._active = active
        self._energy = max(0.0, min(1.0, energy))
        target = 1.0 if active else 0.0
        previous = self._motion
        self._motion += (target - self._motion) * (
            1.0 - math.exp(-dt / 0.12) if dt > 0.0 else 1.0
        )
        if active:
            # Faster when there is voice, slow breathing when idle-listening.
            self._phase += dt * (1.6 + 3.2 * self._energy)
        elif self._motion > 0.002:
            self._phase += dt * 1.6 * self._motion
        if not active and self._motion <= 0.002 and previous <= 0.002:
            return True
        self.update()
        return False

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        # Drift and brightness both scale with motion, so there is no branch
        # between "moving" and "still" to snap across: at zero motion this
        # draws exactly the settled cluster.
        motion = max(0.0, min(1.0, self._motion))
        for angle_frac, rad_frac in self._DOTS:
            angle = angle_frac * 2.0 * math.pi
            bx = cx + math.cos(angle) * self._RX
            by = cy + math.sin(angle) * self._RY
            i = angle_frac * 7.0
            dx = math.sin(self._phase * 2.1 + i * 1.7) * (2.0 + 7.0 * self._energy) * motion
            dy = math.cos(self._phase * 1.7 + i * 2.3) * (1.5 + 5.0 * self._energy) * motion
            swell = 1.0 + 0.28 * math.sin(self._phase * 3.0 + i) * (0.35 + self._energy) * motion
            alpha = 150 + int((65 + 40 * math.sin(self._phase * 2.0 + i * 2.1)) * motion)
            r = self._BASE_R * rad_frac * swell
            colour = QColor(self._tint)
            colour.setAlpha(max(0, min(255, alpha)))
            p.setBrush(colour)
            p.drawEllipse(QPointF(bx + dx, by + dy), r, r)


class RecordingOverlay(QWidget):
    """Black pill status indicator; auto-hides when idle."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("overlay")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        # The window itself paints nothing: with per-pixel transparency the
        # margin around the pill stays invisible and only the pill shows.
        self.setAutoFillBackground(False)
        # The window is the shadow; the pill sits inset inside it.
        self.setFixedSize(
            CARD_WIDTH + SHADOW_SPREAD * 2, CARD_HEIGHT + SHADOW_SPREAD * 2
        )
        self.setWindowOpacity(0.0)

        self._state = "idle"
        self._message = ""
        self._transcript = ""
        self._fading_out = False
        self._session = QElapsedTimer()
        self._session.start()
        self._energy = _Follower()
        self._level_target = 0.0
        self._tick_at = self._session.elapsed() / 1000.0
        # Vertical edge of the screen the pill hugs. Horizontal is always
        # centered; only bottom vs top is a choice.
        self._position = "bottom"

        self._build()
        self._animate()
        self.setStyleSheet(_stylesheet())
        self._apply_state()
        self._reposition()
        self.hide()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(
            SHADOW_SPREAD, SHADOW_SPREAD, SHADOW_SPREAD, SHADOW_SPREAD
        )

        self._card = QFrame(self)
        self._card.setObjectName("card")
        self._card.setFixedSize(CARD_WIDTH, CARD_HEIGHT)
        outer.addWidget(self._card)

        row = QHBoxLayout(self._card)
        row.setContentsMargins(PAD, 0, PAD, 0)
        row.setSpacing(GAP)

        self._dots = SiriDots(self._card)
        row.addWidget(self._dots, 0, Qt.AlignmentFlag.AlignVCenter)

        self._main = ElidedLabel("", max_lines=1, parent=self._card)
        self._main.setObjectName("main")
        row.addWidget(self._main, 1, Qt.AlignmentFlag.AlignVCenter)

        # Hide after a while. Owned so new state can cancel a pending hide.
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(lambda: self.set_state("idle"))

        # Drives the dots at ~60 Hz, only while visible.
        self._meter = QTimer(self)
        self._meter.setInterval(16)
        self._meter.timeout.connect(self._tick)

    def _animate(self) -> None:
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.finished.connect(self._on_fade_finished)

        # Rise: the pill travels a few pixels vertically while it fades, which
        # is what separates "appears" from "fades in". Animating geometry
        # rather than pos: pos is unreliable on a translucent top-level window.
        self._rise = QPropertyAnimation(self, b"geometry", self)

    # ------------------------------------------------------------------ state
    def is_alive(self) -> bool:
        """False once the underlying C++ object is gone."""
        try:
            self.objectName()
            return True
        except RuntimeError:
            return False

    def current_state(self) -> str:
        return self._state

    def set_state(self, state: str, message: str = "") -> None:
        """state: idle | recording | transcribing | done | error"""
        if not self.is_alive():
            return
        self._hide_timer.stop()

        previous = self._state
        self._state = state
        self._message = message

        if state != previous:
            self._apply_state()

        if state == "recording" and previous != "recording":
            self._energy.snap(0.0)

        if state == "idle":
            self._hide()
        else:
            # The meter may have stopped on a settled static state; a new
            # state always means motion again.
            self._tick_at = self._session.elapsed() / 1000.0
            self._meter.start()
            self._show()

    def set_transcript(self, text: str) -> None:
        if not self.is_alive():
            return
        self._transcript = text
        if self._state == "done":
            self._main.set_text(self._caption())

    def set_level(self, level: float) -> None:
        """Feed a microphone level; the dots ease toward it, never snap."""
        self._level_target = (
            0.0 if self._state != "recording" else max(0.0, min(1.0, float(level)))
        )

    def set_position(self, where: str) -> None:
        """Move the pill to the `"bottom"` or `"top"` edge, centered.

        Unknown values are ignored so a hand-edited config cannot park the
        pill off-screen. Takes effect immediately, even mid-show.
        """
        if where not in ("bottom", "top"):
            return
        self._position = where
        self._reposition()

    # ------------------------------------------------------------------ tick
    def _tick(self) -> None:
        now = self._session.elapsed() / 1000.0
        dt = min(0.1, max(0.0, now - self._tick_at))
        self._tick_at = now
        if self._state == "recording":
            target = self._level_target
            active = True
        elif self._state == "transcribing":
            target, active = 0.35, True
        else:
            target, active = 0.0, False
        energy = self._energy.to(target, dt)
        if not active and energy <= 0.002:
            self._energy.snap(0.0)
            energy = 0.0
        if self._dots.tick(dt, energy, active) and not active:
            # Fully settled and nothing left to show: stop paying for frames.
            self._meter.stop()

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:
        """The drop shadow, and nothing else. The pill itself is pure QSS."""
        paint_shadow(
            QPainter(self),
            QRectF(SHADOW_SPREAD, SHADOW_SPREAD, CARD_WIDTH, CARD_HEIGHT),
            CORNER_RADIUS,
            spread=SHADOW_SPREAD,
        )

    # ------------------------------------------------------------------ style
    def _apply_state(self) -> None:
        """Push state into the pill: dots tint plus the one line of text."""
        self._card.setProperty("state", self._state)
        self._dots.set_tint(self._state)
        style = self.style()
        style.unpolish(self._card)
        style.polish(self._card)
        self._main.set_text(self._caption())

    # ------------------------------------------------------------------ copy
    def _caption(self) -> str:
        if self._state == "recording":
            return "Listening…"
        if self._state == "transcribing":
            return "Hearing…"
        if self._state == "error":
            return self._message or "Something went wrong"
        if self._transcript:
            return self._transcript
        return self._message

    # ------------------------------------------------------------------ show
    def _show(self) -> None:
        if self.isVisible():
            return
        self._fading_out = False
        self._reposition()
        self.show()
        self._tick_at = self._session.elapsed() / 1000.0
        self._meter.start()
        self._fade.stop()
        self._fade.setDuration(0 if material.reduced_motion() else FADE_IN[0])
        self._fade.setEasingCurve(FADE_IN[1])
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        if not material.reduced_motion():
            self._rise.stop()
            self._rise.setDuration(FADE_IN[0])
            self._rise.setEasingCurve(FADE_IN[1])
            final = self.geometry()
            start = final.translated(0, 6)
            self._rise.setStartValue(start)
            self._rise.setEndValue(final)
            self._rise.start()

    def _hide(self, immediate: bool = False) -> None:
        """Fade out, then actually hide.

        Only shutdown-adjacent paths hide immediately; the normal idle path
        animates, because blinking out reads as a glitch next to a fade-in.
        """
        if immediate or material.reduced_motion() or not self.isVisible():
            self._fade.stop()
            self.setWindowOpacity(0.0)
            self._meter.stop()
            self.hide()
            return
        self._fading_out = True
        self._fade.stop()
        self._fade.setDuration(FADE_OUT[0])
        self._fade.setEasingCurve(FADE_OUT[1])
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()
        if not material.reduced_motion():
            self._rise.stop()
            self._rise.setDuration(FADE_OUT[0])
            self._rise.setEasingCurve(FADE_OUT[1])
            start = self.geometry()
            end = start.translated(0, 6)
            self._rise.setStartValue(start)
            self._rise.setEndValue(end)
            self._rise.start()

    def _on_fade_finished(self) -> None:
        if self._fading_out and self._fade.endValue() == 0.0:
            self._fading_out = False
            self._meter.stop()
            self.hide()

    # ------------------------------------------------------------------ hide
    def schedule_hide(self, ms: int = 3500) -> None:
        """Return to idle after `ms`, unless new state arrives first."""
        if not self.is_alive():
            return
        self._hide_timer.start(ms)

    # ----------------------------------------------------------------- layout
    def _reposition(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:  # pragma: no cover - headless
            return
        area = screen.availableGeometry()
        x = area.center().x() - self.width() // 2
        if self._position == "top":
            y = area.top() + 48
        else:
            y = area.bottom() - self.height() - 48
        self.move(x, y)
