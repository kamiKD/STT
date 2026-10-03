"""Widgets shared by more than one surface.

Small on purpose: anything in here exists because neither QSS nor a bare
QLabel can express it, not because it is a component library.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, Property, QPropertyAnimation, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QLabel

from . import material


def paint_shadow(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    *,
    spread: float = 20.0,
    rings: int = 16,
    peak: int = 105,
    falloff: float = 2.4,
) -> None:
    """A soft drop shadow, by stacking translucent rounded rects.

    Style sheets cannot express a blur, and a 1px border on a 22px radius is
    the classic source of corner artifacts - Qt strokes the border as an inset
    that does not follow the background's own path. A shadow gives the card its
    separation from any background without putting a hard edge on it, and it is
    drawn on the parent *behind* the card, so the card itself stays pure QSS.

    Rings go from outermost to innermost, so the darkest, tightest part lands
    last on top of the faint halo - which is how a real shadow reads.
    """
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    for index in range(rings, 0, -1):
        distance = spread * index / rings
        alpha = int(peak * (1.0 - index / rings) ** falloff)
        if alpha <= 0:
            continue
        painter.setBrush(QColor(0, 0, 0, alpha))
        grown = rect.adjusted(-distance, -distance, distance, distance)
        painter.drawRoundedRect(grown, radius + distance, radius + distance)
    painter.restore()


class ElidedLabel(QLabel):
    """A label that ends in an ellipsis instead of stopping mid-word.

    `QLabel` clips silently: a long transcript or error message loses its tail
    with nothing to say that anything was dropped, which is worse than a
    shorter message. Wraps to at most `max_lines`, eliding the last one that
    does not fit.

    `sizeHint` reports room for `max_lines` regardless of the current text.
    Without that the layout sizes the label to its one-line hint and the second
    line is clipped by the widget itself - which looks exactly like the silent
    truncation this class exists to prevent.
    """

    def __init__(self, text: str = "", max_lines: int = 2, parent=None):
        super().__init__(text, parent)
        self._max_lines = max(1, max_lines)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        # Crossfade: pulse opacity 1→0→1. A custom property rather than
        # QGraphicsOpacityEffect, which renders the whole label invisible even at
        # opacity 1.0 when the widget paints itself. The backing field has a
        # different name from the Qt property, because Property intercepts
        # assignment and would recurse through the setter.
        self._opacity_value = 1.0
        self._fade = QPropertyAnimation(self, b"_opacity", self)
        self._fade.setDuration(120)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._fade.finished.connect(self._on_fade_finished)

    def get_opacity(self) -> float:
        return self._opacity_value

    def set_opacity(self, value: float) -> None:
        self._opacity_value = max(0.0, min(1.0, float(value)))
        self.update()

    _opacity = Property(float, get_opacity, set_opacity)

    def set_text(self, text: str) -> None:
        """Set `text` immediately, then pulse opacity 1→0→1.

        The text swaps at once so callers never see a stale label; the opacity
        pulse is what reads as a transition rather than a blink.
        """
        if text == self.text():
            self._fade.stop()
            self.set_opacity(1.0)
            return
        self.setText(text)
        if material.reduced_motion():
            return
        self._fade.stop()
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._fade.endValue() != 0.0:
            return
        self._fade.stop()
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def lines(self) -> list[str]:
        """The text wrapped to this widget's width, elided if it runs over."""
        metrics = self.fontMetrics()
        width = max(1, self.width())
        wrapped: list[str] = []
        current = ""
        for word in self.text().split():
            candidate = f"{current} {word}".strip()
            if current and metrics.horizontalAdvance(candidate) > width:
                wrapped.append(current)
                current = word
            else:
                current = candidate
        if current or not wrapped:
            wrapped.append(current)

        if len(wrapped) <= self._max_lines:
            return wrapped
        kept = wrapped[: self._max_lines - 1]
        kept.append(
            metrics.elidedText(
                " ".join(wrapped[self._max_lines - 1 :]),
                Qt.TextElideMode.ElideRight,
                width,
            )
        )
        return kept

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setOpacity(self._opacity)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(
            self.rect(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            | int(Qt.TextFlag.TextWordWrap),
            "\n".join(self.lines()),
        )

    def sizeHint(self):
        hint = super().sizeHint()
        height = self.fontMetrics().lineSpacing() * self._max_lines
        if height > 0:
            hint.setHeight(int(height))
        return hint

    def minimumSizeHint(self):
        return self.sizeHint()