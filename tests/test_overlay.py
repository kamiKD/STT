"""Tests for the pill overlay: a black stadium with dots and one line of text.

The Qt event loop is driven for real, so the fade and the dot driver run
exactly as on screen. These lock the behaviour that is easy to break and hard
to see: the stadium geometry, the single line of copy per state, the dots
easing toward voice energy instead of snapping, and the auto-hide.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("pytestqt")

from PySide6.QtCore import QEasingCurve, QEventLoop, Qt, QTimer  # noqa: E402

from stt import material  # noqa: E402
from stt.overlay import (  # noqa: E402
    ACCENT_ERROR,
    CARD_HEIGHT,
    CARD_WIDTH,
    CORNER_RADIUS,
    DOT_TINT_DEFAULT,
    DOT_TINTS,
    SHADOW_SPREAD,
    RecordingOverlay,
    _stylesheet,
)


def _drain(ms=260):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture
def card(qtbot):
    widget = RecordingOverlay()
    qtbot.addWidget(widget)
    return widget


# ------------------------------------------------------------------ geometry
def test_a_new_pill_is_hidden_and_idle(card):
    assert card.isVisible() is False
    assert card.current_state() == "idle"


def test_the_pill_is_a_stadium():
    """Radius is exactly half the height: no corner radii to disagree about."""
    assert CORNER_RADIUS == CARD_HEIGHT // 2
    assert CARD_WIDTH > CARD_HEIGHT * 3


def test_the_pill_has_a_fixed_card_size(card):
    assert card._card.width() == CARD_WIDTH
    assert card._card.height() == CARD_HEIGHT
    assert card.width() == CARD_WIDTH + SHADOW_SPREAD * 2
    assert card.height() == CARD_HEIGHT + SHADOW_SPREAD * 2


def test_the_pill_proportions_suit_a_1080p_screen():
    """~5.8:1. The old 6.9:1 read squashed on 1920x1080."""
    ratio = CARD_WIDTH / CARD_HEIGHT
    assert 5.0 < ratio < 6.5, f"pill is {CARD_WIDTH}x{CARD_HEIGHT} ({ratio:.1f}:1)"
    assert CORNER_RADIUS == CARD_HEIGHT // 2


def test_the_dots_fit_their_widget_at_full_energy():
    """Worst-case drift plus radius must stay inside the widget bounds."""
    from stt.overlay import DOTS_H, DOTS_W, SiriDots

    drift = 2.0 + 7.0 * 1.0
    radius = SiriDots._BASE_R * 1.05 * (1.0 + 0.28 * 1.35)
    assert SiriDots._RX + drift + radius <= DOTS_W / 2
    assert SiriDots._RY + (1.5 + 5.0 * 1.0) + radius <= DOTS_H / 2 + 1.0


# ------------------------------------------------------------------ position
def _fake_screen(monkeypatch, width=1920, height=1080):
    """A deterministic desktop: the real one varies per machine."""
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    geom = QRect(0, 0, width, height)
    fake = SimpleNamespace(availableGeometry=lambda: geom)
    monkeypatch.setattr(
        QApplication, "primaryScreen", staticmethod(lambda: fake)
    )
    return geom


def test_the_pill_defaults_to_bottom_center(card, monkeypatch):
    geom = _fake_screen(monkeypatch)
    card.set_position("bottom")
    assert card._position == "bottom"
    # QRect is inclusive: a 1920-wide rect centers at 959 and ends at 1079.
    assert card.x() == geom.center().x() - card.width() // 2
    assert card.y() == geom.bottom() - card.height() - 48


def test_the_pill_moves_to_top_center(card, monkeypatch):
    geom = _fake_screen(monkeypatch)
    card.set_position("top")
    assert card._position == "top"
    assert card.x() == geom.center().x() - card.width() // 2
    assert card.y() == geom.top() + 48


def test_the_position_survives_a_round_trip(card, monkeypatch):
    geom = _fake_screen(monkeypatch)
    card.set_position("top")
    card.set_position("bottom")
    assert card._position == "bottom"
    assert card.y() == geom.bottom() - card.height() - 48


def test_an_unknown_position_is_ignored(card, monkeypatch):
    """A hand-edited config must not park the pill off-screen."""
    _fake_screen(monkeypatch)
    card.set_position("top")
    card.set_position("left")
    assert card._position == "top"
    assert card.y() == 48


def test_the_window_leaves_room_for_the_shadow(card, qtbot):
    m = card.layout().contentsMargins()
    assert (m.left(), m.top(), m.right(), m.bottom()) == (
        SHADOW_SPREAD,
        SHADOW_SPREAD,
        SHADOW_SPREAD,
        SHADOW_SPREAD,
    )


def test_the_compositor_is_not_asked_to_clip_the_corners():
    import inspect

    assert "round_corners" not in inspect.getsource(RecordingOverlay)


def test_it_never_takes_focus(card):
    flags = card.windowFlags()
    assert flags & Qt.WindowType.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool
    assert card.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert card.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)


def test_the_style_sheet_has_no_border_and_a_stadium_radius():
    sheet = _stylesheet()
    assert f"border-radius: {CORNER_RADIUS}px" in sheet
    assert "border: none" in sheet


def test_the_pill_is_near_opaque_black_over_transparency():
    """Only the pill is ever visible: no backdrop, no gray frame.

    The window margin stays transparent (the widget paints nothing and fills
    nothing itself); the pill carries a near-opaque black so white text holds.
    """
    sheet = _stylesheet()
    fill = sheet.split("background-color: rgba(")[1].split(")")[0]
    alpha = int(fill.split(", ")[-1])
    assert alpha > 200, "a translucent fill lets the desktop muddy the black"
    assert "border: none" in sheet


def test_both_surfaces_share_one_fill():
    """The pill and the dialog cannot drift apart in darkness."""
    from stt.hotkey_dialog import _stylesheet as dialog_stylesheet

    assert material.PILL_FILL in _stylesheet()
    assert material.PILL_FILL in dialog_stylesheet()


def test_the_style_sheet_is_applied_at_construction(card):
    assert card.styleSheet()
    assert "border-radius" in card.styleSheet()


def test_the_window_paints_and_fills_nothing_itself(card):
    """The margin around the pill must stay invisible: no gray frame."""
    assert card.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert not card.autoFillBackground()


def test_the_style_sheet_names_a_real_font_family():
    sheet = _stylesheet()
    assert "font-family" in sheet
    assert material.font_family() in sheet


def test_the_main_line_is_a_single_line(card):
    assert card._main._max_lines == 1


# ---------------------------------------------------------------------- copy
def test_setting_a_state_shows_the_pill(card):
    card.set_state("recording")
    assert card.isVisible() is True


def test_recording_says_listening(card):
    card.set_state("recording")
    assert card._main.text() == "Listening…"


def test_transcribing_says_hearing(card):
    """While the audio is with Groq, the pill says what it is doing."""
    card.set_state("transcribing")
    assert card._main.text() == "Hearing…"


def test_done_shows_the_transcript(card):
    card.set_state("recording")
    card.set_transcript("hello there")
    card.set_state("done")
    assert card._main.text() == "hello there"


def test_transcript_only_lands_on_done(card):
    card.set_state("recording")
    card.set_transcript("hello there")
    assert card._main.text() == "Listening…"


def test_an_error_message_is_the_copy(card):
    card.set_state("done")
    card.set_transcript("the transcript")
    card.set_state("error", "Groq rate limit hit.")
    assert card._main.text() == "Groq rate limit hit."


def test_a_long_line_is_elided(card):
    card.set_state("done")
    card.set_transcript("word " * 200)
    assert len(card._main.lines()) == 1
    assert card._main.lines()[0].endswith("…")


def test_going_idle_fades_out_then_hides(card):
    """The normal idle path animates; only shutdown-adjacent paths skip it."""
    card.set_state("recording")
    card.set_state("idle")
    assert card.isVisible() is True, "an animated hide is still on screen"
    assert card._fade.state() == card._fade.State.Running
    _drain(400)
    assert card.isVisible() is False


def test_only_failure_takes_a_tint():
    assert DOT_TINTS == {"error": ACCENT_ERROR}
    assert DOT_TINT_DEFAULT == "#ffffff"


# ---------------------------------------------------------------------- dots
def test_the_dots_move_only_while_listening_or_searching(card):
    card.set_state("recording")
    before = card._dots._phase
    _drain(120)
    assert card._dots._phase > before

    card.set_state("done", "Inserted")
    _drain(900)  # past the coast-down: motion eases to zero, then stops
    frozen = card._dots._phase
    _drain(200)
    assert card._dots._phase == pytest.approx(frozen)


def test_leaving_a_state_coasts_instead_of_snapping(card):
    """The phase keeps advancing while residual motion drains."""
    card.set_state("recording")
    card.set_level(1.0)
    _drain(400)
    card.set_state("done", "Inserted")
    coasting = card._dots._phase
    _drain(60)
    assert card._dots._phase > coasting, "froze mid-drift instead of coasting"


def test_the_dots_keep_shimmering_while_searching(card):
    card.set_state("transcribing")
    before = card._dots._phase
    _drain(120)
    assert card._dots._phase > before


def test_voice_energy_eases_instead_of_snapping(card):
    card.set_state("recording")
    card.set_level(1.0)
    assert card._dots._energy == 0.0
    _drain(80)
    assert 0.0 < card._dots._energy < 1.0


def test_the_level_is_clamped(card):
    card.set_state("recording")
    card.set_level(5.0)
    assert card._level_target == 1.0
    card.set_level(-5.0)
    assert card._level_target == 0.0


def test_energy_drains_when_recording_stops(card):
    card.set_state("recording")
    card.set_level(1.0)
    _drain(500)
    assert card._dots._energy > 0.8
    card.set_state("done", "Inserted")
    _drain(600)
    assert card._dots._energy == pytest.approx(0.0, abs=0.08)


def test_the_meter_only_runs_while_something_moves(card):
    assert not card._meter.isActive()
    card.set_state("recording")
    assert card._meter.isActive()
    card.set_state("done", "Inserted")
    _drain(900)  # coast-down, then the settled tick stops the meter
    assert not card._meter.isActive()
    card.set_state("idle")
    _drain(500)
    assert not card._meter.isActive()
    assert card.isVisible() is False


def test_a_settled_tick_skips_its_repaint(card, monkeypatch):
    """A static pill must not repaint 60 times a second for nothing."""
    card.set_state("done", "Inserted")
    _drain(900)
    repaints = []
    monkeypatch.setattr(card._dots, "update", lambda *a: repaints.append(1))
    assert card._dots.tick(0.016, 0.0, False) is True
    assert repaints == []


def test_the_dots_take_the_state_tint(card):
    card.set_state("error", "Nope")
    assert card._dots._tint.name() == "#ffb340"


# ---------------------------------------------------------------------- fade
def test_the_fade_is_configured_to_actually_travel(card):
    card.set_state("done", "Inserted")
    assert card._fade.state() == card._fade.State.Running
    assert card._fade.startValue() == pytest.approx(0.0)
    assert card._fade.endValue() == pytest.approx(1.0)
    assert card._fade.duration() == 200
    assert card._fade.easingCurve().type() == QEasingCurve.Type.OutCubic


def test_the_fade_animates_window_opacity(card):
    assert card._fade.targetObject() is card
    assert bytes(card._fade.propertyName()) == b"windowOpacity"


def test_reduced_motion_collapses_the_fade(card, monkeypatch):
    monkeypatch.setattr(material, "reduced_motion", lambda: True)
    card.set_state("idle")
    card.set_state("recording")
    assert card._fade.duration() == 0


def test_without_reduced_motion_the_fade_lasts(card, monkeypatch):
    monkeypatch.setattr(material, "reduced_motion", lambda: False)
    card.set_state("recording")
    assert card._fade.duration() == 200


# ------------------------------------------------------------------- crossfade
def test_text_crossfades_instead_of_blinking(card):
    """The label must dip in opacity while the text swaps, never sit at 1."""
    card.set_state("recording")
    card._main.set_text("Hearing…")
    assert card._main._fade.state() == card._main._fade.State.Running
    _drain(40)  # let the fade actually dip
    assert card._main._opacity < 1.0
    _drain(400)
    assert card._main.text() == "Hearing…"
    assert card._main._opacity == pytest.approx(1.0, abs=1e-6)


def test_crossfade_is_skipped_when_text_is_unchanged(card):
    card.set_state("recording")
    card._main.set_text("Listening…")
    assert card._main._fade.state() == card._main._fade.State.Stopped
    assert card._main._opacity == pytest.approx(1.0, abs=1e-6)


def test_reduced_motion_swaps_text_without_fading(card, monkeypatch):
    monkeypatch.setattr(material, "reduced_motion", lambda: True)
    card.set_state("recording")
    card._main.set_text("Hearing…")
    assert card._main.text() == "Hearing…"
    assert card._main._opacity == pytest.approx(1.0, abs=1e-6)


# ----------------------------------------------------------------------- rise
def test_entrance_rises_while_it_fades(card):
    """The pill must travel vertically, not just change opacity."""
    card.set_state("recording")
    assert card._rise.state() == card._rise.State.Running
    start = card._rise.startValue()
    end = card._rise.endValue()
    assert start.y() == end.y() + 6, "entrance should start 6px lower"
    _drain(400)
    assert card.pos().y() == end.y()


def test_exit_sinks_while_it_fades(card):
    card.set_state("recording")
    _drain(400)
    card.set_state("idle")
    assert card._rise.state() == card._rise.State.Running
    start = card._rise.startValue()
    end = card._rise.endValue()
    assert end.y() == start.y() + 6, "exit should end 6px lower"
    _drain(400)


def test_reduced_motion_skips_the_rise(card, monkeypatch):
    monkeypatch.setattr(material, "reduced_motion", lambda: True)
    card.set_state("recording")
    assert card._rise.state() == card._rise.State.Stopped


# ---------------------------------------------------------------------- hide
def test_a_pending_hide_is_cancelled_by_new_state(card):
    card.set_state("done", "Inserted")
    card.schedule_hide(40)
    card.set_state("recording")
    _drain(140)
    assert card.current_state() == "recording"
    assert card.isVisible() is True


def test_the_hide_timer_returns_the_pill_to_idle(card):
    card.set_state("done", "Inserted")
    card.schedule_hide(40)
    _drain(500)  # hide delay, then the 140 ms fade-out
    assert card.current_state() == "idle"
    assert card.isVisible() is False


def test_a_deleted_pill_does_not_raise(qtbot):
    card = RecordingOverlay()
    qtbot.addWidget(card)
    card.set_state("recording")
    card.deleteLater()
    assert card.is_alive() in (True, False)
