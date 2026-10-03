"""Integration tests for the App controller pipeline.

Drives the real App object with a Qt event loop: hotkey press/release, the
recorder, the transcription worker, and delivery into the focused window. Only
the network call and the actual keystrokes are replaced.
"""

from __future__ import annotations

import numpy as np
import pytest
import pytestqt  # noqa: F401  (provides the `qtbot` fixture)
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QDialog

from stt import config as config_mod
from stt import transcribe, typing, voice_command
from stt.app import MODE_COMMAND, App

pytest.importorskip("pytestqt")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    conf = config_mod.load_config()
    conf["auto_type"] = True
    conf["min_rms"] = 0.0  # let the test's silent buffer through
    return conf


@pytest.fixture
def app(qtbot, cfg, monkeypatch):
    delivered = []

    monkeypatch.setattr(
        typing, "deliver", lambda text, **kw: delivered.append((text, kw)) or "typed"
    )

    controller = App(cfg)
    qtbot.addWidget(controller.overlay)
    # The real key hook is not needed: tests call the handlers directly.
    controller.listener.stop()
    if controller.command_listener is not None:
        controller.command_listener.stop()
    yield controller, delivered
    controller.shutdown()


def _drain(qtbot, ms=1500):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _speak(controller, seconds=0.3):
    """Simulate a recording with a loud enough signal to pass the RMS gate."""
    n = int(controller.cfg["sample_rate"] * seconds)
    controller.recorder._chunks = [np.full(n, 8000, dtype=np.int16)]
    controller.recorder._frames = n
    controller.recorder._active = True


def test_press_starts_recording_and_shows_overlay(app, qtbot):
    controller, _ = app
    controller._on_press()
    assert controller.recorder.is_recording
    assert controller.overlay.current_state() == "recording"
    controller.recorder.abort()


def test_release_sends_audio_and_types_transcript(app, qtbot, monkeypatch):
    controller, delivered = app
    captured = {}

    def fake_transcribe(wav_bytes, api_key, model, language, prompt, timeout=60):
        captured["api_key"] = api_key
        captured["model"] = model
        captured["language"] = language
        captured["wav"] = wav_bytes
        return "test transcript"

    monkeypatch.setattr(transcribe, "transcribe", fake_transcribe)

    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)

    assert captured["api_key"] == "gsk_test_key"
    assert captured["model"] == controller.cfg["model"]
    assert captured["language"] == controller.cfg["language"]
    assert captured["wav"][:4] == b"RIFF"
    assert delivered and delivered[0][0] == "test transcript "  # trailing space
    assert controller.overlay.current_state() == "done"


def test_trailing_space_can_be_disabled(app, qtbot, monkeypatch):
    controller, delivered = app
    controller.cfg["trailing_space"] = False
    monkeypatch.setattr(
        transcribe, "transcribe", lambda wav, **kw: "no space please"
    )
    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)
    assert delivered[0][0] == "no space please"


# --------------------------------------------------- focus held during typing
@pytest.fixture
def focus(app, monkeypatch):
    """Pin the foreground window and let the test move it between press/release.

    Transcribing takes a second or two. Without a check, switching windows in
    that gap typed the transcript into whatever happened to be in front.
    """
    controller, delivered = app
    state = {"hwnd": 1001}
    monkeypatch.setattr(typing, "foreground_window", lambda: state["hwnd"])
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, **kw: "typed here")
    copied = []
    monkeypatch.setattr(
        typing, "copy_to_clipboard", lambda text: copied.append(text) or True
    )
    return controller, delivered, state, copied


def test_same_focus_inserts_as_usual(focus, qtbot):
    controller, delivered, _state, copied = focus
    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)
    assert delivered[0][0] == "typed here "
    assert copied == []


def test_a_window_switch_stops_the_insertion(focus, qtbot):
    controller, delivered, state, copied = focus
    controller._on_press()
    state["hwnd"] = 2002  # user alt-tabbed while Groq was thinking
    _speak(controller)
    controller._on_release()
    _drain(qtbot)

    assert delivered == []  # nothing typed anywhere
    assert copied == ["typed here "]  # but the text is recoverable
    assert controller.overlay.current_state() == "error"


def test_the_focus_check_is_reset_between_dictations(focus, qtbot):
    """A stale hwnd must not poison the next, legitimate insertion."""
    controller, delivered, state, _copied = focus
    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)
    assert len(delivered) == 1

    state["hwnd"] = 3003
    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)
    assert len(delivered) == 2


def test_an_unknown_foreground_window_is_not_treated_as_a_switch(focus, qtbot):
    """hwnd 0 means Windows would not say; that is not a focus change."""
    controller, delivered, state, _copied = focus
    controller._on_press()
    state["hwnd"] = 0
    _speak(controller)
    controller._on_release()
    _drain(qtbot)
    assert delivered[0][0] == "typed here "


def test_a_blocked_injection_is_reported_as_a_problem(focus, qtbot, monkeypatch):
    """SendInput refused the keystrokes: say so instead of claiming success."""
    controller, delivered, _state, _copied = focus

    def blocked(*a, **kw):
        raise typing.InjectionBlocked("UIPI")

    monkeypatch.setattr(typing, "deliver", blocked)
    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)

    assert delivered == []
    assert controller.overlay.current_state() == "error"
    assert controller._busy is False


def test_command_mode_ignores_the_focus_check(focus, qtbot, monkeypatch):
    """Nothing is typed on this path, so the window does not matter."""
    controller, delivered, state, copied = focus
    ran = []

    def execute(text, **kw):
        ran.append(text)
        return voice_command.Resolution(ok=True, name="Opened")

    monkeypatch.setattr(voice_command, "execute_text", execute)
    controller.cfg["command_with_voice"] = True
    controller._on_press(MODE_COMMAND)
    state["hwnd"] = 9999
    _speak(controller)
    controller._on_release(MODE_COMMAND)
    _drain(qtbot)

    assert ran == ["typed here"]
    assert delivered == []
    assert copied == []
    assert controller.overlay.current_state() == "done"


def test_silence_is_rejected_before_any_api_call(app, qtbot, monkeypatch):
    controller, delivered = app
    controller.cfg["min_rms"] = 0.5  # nothing will pass
    called = []
    monkeypatch.setattr(
        transcribe, "transcribe", lambda wav, **kw: called.append(wav) or "x"
    )

    controller._on_press()
    n = int(controller.cfg["sample_rate"] * 0.2)
    controller.recorder._chunks = [np.zeros(n, dtype=np.int16)]
    controller.recorder._frames = n
    controller.recorder._active = True
    controller._on_release()
    _drain(qtbot, 400)

    assert called == []
    assert delivered == []
    assert controller.overlay.current_state() == "error"


def test_api_error_is_surfaced_not_typed(app, qtbot, monkeypatch):
    controller, delivered = app

    def boom(wav, **kwargs):
        raise transcribe.TranscriptionError("Groq rate limit hit.")

    monkeypatch.setattr(transcribe, "transcribe", boom)

    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)

    assert delivered == []
    assert controller.overlay.current_state() == "error"


def test_busy_flag_clears_after_failure(app, qtbot, monkeypatch):
    controller, _ = app
    monkeypatch.setattr(
        transcribe,
        "transcribe",
        lambda wav, **kw: (_ for _ in ()).throw(transcribe.TranscriptionError("nope")),
    )
    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)
    assert controller._busy is False


def test_missing_api_key_refuses_to_record(app, qtbot):
    controller, _ = app
    controller.api_key = ""
    controller._on_press()
    assert controller.recorder.is_recording is False
    assert controller.overlay.current_state() == "error"


def test_disabled_mode_ignores_hotkey(app, qtbot):
    controller, _ = app
    controller.cfg["auto_type"] = False
    controller._on_press()
    assert controller.recorder.is_recording is False


def test_max_duration_timer_starts_on_press(app, qtbot):
    controller, _ = app
    controller._on_press()
    assert controller._max_timer.isActive()
    assert controller._max_timer.interval() == controller.cfg["max_seconds"] * 1000 + 1500
    controller._on_release()


def test_max_duration_timer_stops_on_release(app, qtbot, monkeypatch):
    controller, _ = app
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, **kw: "done")
    controller._on_press()
    _speak(controller)
    controller._on_release()
    assert not controller._max_timer.isActive()


def test_force_release_rescues_a_lost_keyup(app, qtbot, monkeypatch):
    """The timeout must drive a normal release, not an error path."""
    controller, delivered = app
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, **kw: "rescued text")
    controller._on_press()
    _speak(controller)
    controller._force_release()  # stands in for the timer firing
    _drain(qtbot)
    assert delivered and delivered[0][0] == "rescued text "


def test_force_release_is_noop_when_not_recording(app, qtbot):
    controller, _ = app
    controller._force_release()  # must not raise
    assert controller.overlay.current_state() == "idle"


def test_release_without_press_is_safe(app, qtbot):
    controller, _ = app
    controller._on_release()  # must not raise
    assert controller.overlay.current_state() == "idle"


def test_transcribe_uses_worker_thread_not_gui(app, qtbot, monkeypatch):
    """Transcription must not run on the Qt thread, or the overlay freezes."""
    controller, _ = app
    import threading

    seen = {}

    def probe(wav, **kwargs):
        seen["thread"] = threading.current_thread().ident
        return "ok"

    monkeypatch.setattr(transcribe, "transcribe", probe)
    main_thread = threading.current_thread().ident

    controller._on_press()
    _speak(controller)
    controller._on_release()
    _drain(qtbot)

    assert seen["thread"] != main_thread


# --------------------------------------------------------- command voice mode
@pytest.fixture
def spoken(monkeypatch):
    """Replace the action layer and record what was said."""
    acted = []

    def fake(text, **kwargs):
        acted.append(text)
        return voice_command.Resolution(
            ok=True, name="Chrome", target="chrome.exe", kind=voice_command.KIND_APP
        )

    monkeypatch.setattr(voice_command, "execute_text", fake)
    return acted


def test_command_listener_exists_when_enabled(app):
    controller, _ = app
    assert controller.command_listener is not None


def test_command_mode_runs_the_command(app, qtbot, monkeypatch, spoken):
    controller, _ = app
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, **kw: "Open Chrome")

    controller._on_press(MODE_COMMAND)
    _speak(controller)
    controller._on_release(MODE_COMMAND)
    _drain(qtbot)

    assert spoken == ["Open Chrome"]
    assert controller.overlay.current_state() == "done"


def test_command_mode_never_types_into_the_focused_window(app, qtbot, monkeypatch, spoken):
    """The whole point: the page you are on keeps its content."""
    controller, delivered = app
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, **kw: "close chrome")

    controller._on_press(MODE_COMMAND)
    _speak(controller)
    controller._on_release(MODE_COMMAND)
    _drain(qtbot)

    assert delivered == []


def test_command_mode_accepts_portuguese(app, qtbot, monkeypatch, spoken):
    controller, _ = app
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, **kw: "Abra o Chrome")

    controller._on_press(MODE_COMMAND)
    _speak(controller)
    controller._on_release(MODE_COMMAND)
    _drain(qtbot)

    assert spoken == ["Abra o Chrome"]


def test_command_mode_surfaces_a_failure(app, qtbot, monkeypatch):
    controller, _ = app
    monkeypatch.setattr(
        voice_command,
        "execute_text",
        lambda text, **kw: voice_command.Resolution(
            ok=False, message=f'"{text}" is not installed.', suggestions=["Notepad"]
        ),
    )
    monkeypatch.setattr(transcribe, "transcribe", lambda wav, **kw: "open nonesuch")

    controller._on_press(MODE_COMMAND)
    _speak(controller)
    controller._on_release(MODE_COMMAND)
    _drain(qtbot)

    assert controller.overlay.current_state() == "error"


def test_command_hotkey_is_ignored_when_the_feature_is_off(app, qtbot):
    controller, _ = app
    controller.cfg["command_with_voice"] = False
    controller._on_press(MODE_COMMAND)
    assert controller.recorder.is_recording is False


def test_second_press_does_not_restart_the_recorder(app, qtbot):
    """Two listeners on one combo must not stack two captures."""
    controller, _ = app
    controller._on_press()
    controller._on_press(MODE_COMMAND)
    controller.recorder.abort()


def test_capturing_a_hotkey_suppresses_recordings(app):
    controller, _ = app
    controller._capturing = True
    controller._on_press()
    assert controller.recorder.is_recording is False


def test_rebinding_the_hotkey_replaces_the_listener(app):
    controller, _ = app
    old = controller.listener
    controller._bind_hotkey("hotkey", ["ctrl", "shift", "m"])
    assert controller.listener is not old
    assert controller.listener.label == "Ctrl+Shift+M"
    assert controller.listener._listener is not None


def test_rebinding_while_recording_drops_the_buffer(app, qtbot, monkeypatch):
    """Stopping a held listener fires a release; it must not transcribe."""
    controller, delivered = app
    calls = []
    monkeypatch.setattr(
        transcribe, "transcribe", lambda wav, **kw: calls.append(wav) or "text"
    )
    controller._on_press()
    _speak(controller)
    controller._bind_hotkey("hotkey", ["ctrl", "shift", "m"])
    _drain(qtbot, 200)
    assert calls == []
    assert controller.recorder.is_recording is False


# ------------------------------------------------------------------ tray menu
def _menu_text(controller):
    return [action.text() for action in controller._menu.actions() if action.text()]


def _all_menu_text(controller):
    """Text of every action, including submenus."""
    out = []
    for action in controller._menu.actions():
        if action.text():
            out.append(action.text())
        menu = action.menu()
        if menu:
            out.extend(a.text() for a in menu.actions() if a.text())
    return out


def test_tray_menu_has_command_with_voice(app):
    controller, _ = app
    assert "Command with Voice" in _menu_text(controller)


def test_tray_menu_shows_the_command_hotkey(app):
    controller, _ = app
    # The hotkey is no longer a disabled info row; it lives in the Hotkeys submenu.
    assert any("Hotkeys" in text for text in _menu_text(controller))
    all_text = _all_menu_text(controller)
    assert "Dictation..." in all_text
    assert "Command..." in all_text


def test_tray_menu_has_both_hotkey_setters(app):
    controller, _ = app
    text = _all_menu_text(controller)
    assert "Dictation..." in text
    assert "Command..." in text


def test_tray_menu_exposes_the_system_command_toggle(app):
    controller, _ = app
    assert "Allow system commands" in _menu_text(controller)


def test_system_toggle_persists(app):
    controller, _ = app
    controller._on_toggle_system(False)
    assert config_mod.load_config()["command_system_enabled"] is False


def test_tray_menu_exposes_the_overlay_position(app):
    controller, _ = app
    assert any("Overlay:" in text for text in _menu_text(controller))


def test_tray_menu_groups_rarely_used_settings(app):
    """Advanced items live in a submenu, not the root."""
    controller, _ = app
    root = _menu_text(controller)
    assert "Copy to clipboard as backup" not in root
    assert "Start with Windows" not in root
    assert "Copy last transcript" not in root
    assert "Groq API key" not in root
    all_text = _all_menu_text(controller)
    assert "Copy to clipboard as backup" in all_text
    assert "Start with Windows" in all_text
    assert "Copy last transcript" in all_text
    assert any("Groq API key" in t for t in all_text)


def test_tray_menu_has_no_disabled_info_rows(app):
    """Disabled info rows waste space; the tray tooltip carries the hotkey."""
    controller, _ = app
    assert not any("Hold" in text and "dictate" in text for text in _menu_text(controller))


def test_overlay_position_pick_persists_and_moves_the_pill(app):
    controller, _ = app
    controller._on_pick_position("top")
    assert config_mod.load_config()["overlay_position"] == "top"
    assert controller.overlay._position == "top"
    controller._on_pick_position("bottom")
    assert config_mod.load_config()["overlay_position"] == "bottom"
    assert controller.overlay._position == "bottom"


def test_overlay_position_pick_ignores_garbage(app):
    controller, _ = app
    controller._on_pick_position("top")
    controller._on_pick_position("left")
    assert config_mod.load_config()["overlay_position"] == "top"
    assert controller.overlay._position == "top"
    controller._on_pick_position("bottom")


def test_the_app_applies_the_saved_position_on_startup(cfg, qtbot, monkeypatch):
    """A restart must honour the choice, not reset to the default."""
    from stt.app import App

    cfg["overlay_position"] = "top"
    controller = App(cfg)
    qtbot.addWidget(controller.overlay)
    controller.listener.stop()
    if controller.command_listener is not None:
        controller.command_listener.stop()
    try:
        assert controller.overlay._position == "top"
    finally:
        controller.shutdown()


def test_command_toggle_starts_and_stops_the_second_listener(app):
    controller, _ = app
    controller._on_toggle_command(False)
    assert controller.command_listener is None
    controller._on_toggle_command(True)
    assert controller.command_listener is not None
    controller.command_listener.stop()


def test_command_toggle_persists(app, cfg):
    controller, _ = app
    controller._on_toggle_command(False)
    assert config_mod.load_config()["command_with_voice"] is False


# ------------------------------------------------------- hotkey from the tray
def test_the_index_refresher_outruns_the_cache_ttl():
    """The whole point: the cache must never expire between two commands.

    If the interval were at or above INDEX_TTL, the next voice command would
    rebuild the Start Menu index on the Qt thread - a multi-second freeze.
    """
    from stt.app import INDEX_REFRESH_MS

    assert 0 < INDEX_REFRESH_MS < voice_command.INDEX_TTL * 1000


# ------------------------------------------------------- hotkey from the tray
class _FakeDialog:
    """Stands in for HotkeyDialog so the tests never grab a real keyboard."""

    combo_to_return: list[str] | None = None
    accepted = True
    seen: list["_FakeDialog"] = []

    def __init__(self, title, hint, current=None, parent=None):
        self.title = title
        self.hint = hint
        self.current = current
        _FakeDialog.seen.append(self)

    def exec(self):
        return (
            QDialog.DialogCode.Accepted
            if _FakeDialog.accepted
            else QDialog.DialogCode.Rejected
        )

    def combo(self):
        return _FakeDialog.combo_to_return


@pytest.fixture
def fake_dialog(monkeypatch):
    _FakeDialog.seen = []
    _FakeDialog.combo_to_return = None
    _FakeDialog.accepted = True
    monkeypatch.setattr("stt.app.HotkeyDialog", _FakeDialog)
    return _FakeDialog


def test_tray_hotkey_rebinding_saves_and_rebinds(app, cfg, fake_dialog):
    controller, _ = app
    fake_dialog.combo_to_return = ["ctrl", "win", "k"]
    controller._on_edit_hotkey("hotkey")

    assert controller.cfg["hotkey"] == ["ctrl", "win", "k"]
    assert config_mod.load_config()["hotkey"] == ["ctrl", "win", "k"]
    assert controller.listener.label == "Ctrl+Win+K"


def test_tray_hotkey_rebinding_updates_the_menu(app, fake_dialog):
    controller, _ = app
    fake_dialog.combo_to_return = ["ctrl", "shift", "m"]
    controller._on_edit_hotkey("hotkey")
    # The menu is rebuilt; the hotkey is no longer a disabled info row.
    assert not any("Hold" in text and "dictate" in text for text in _menu_text(controller))
    assert controller._menu.actions(), "menu should have actions"


def test_tray_command_hotkey_rebinds_the_second_listener(app, fake_dialog):
    controller, _ = app
    fake_dialog.combo_to_return = ["ctrl", "win", "j"]
    controller._on_edit_hotkey("command_hotkey")

    assert controller.cfg["command_hotkey"] == ["ctrl", "win", "j"]
    assert controller.command_listener.label == "Ctrl+Win+J"
    # The hotkey is no longer shown as text in the menu.
    assert not any("Ctrl+Win+J" in text for text in _menu_text(controller))


def test_tray_hotkey_cancel_changes_nothing(app, fake_dialog):
    controller, _ = app
    before = list(controller.cfg["hotkey"])
    fake_dialog.accepted = False
    controller._on_edit_hotkey("hotkey")
    assert controller.cfg["hotkey"] == before


def test_tray_hotkey_rejects_a_combo_already_in_use(app, fake_dialog):
    controller, _ = app
    fake_dialog.combo_to_return = list(controller.cfg["command_hotkey"])
    controller._on_edit_hotkey("hotkey")
    assert controller.cfg["hotkey"] != controller.cfg["command_hotkey"]
    assert controller.overlay.current_state() == "error"


def test_tray_clears_the_capture_guard_when_the_dialog_explodes(app, monkeypatch):
    controller, _ = app

    def boom(*args, **kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr("stt.app.HotkeyDialog", boom)
    with pytest.raises(RuntimeError):
        controller._on_edit_hotkey("hotkey")
    assert controller._capturing is False
