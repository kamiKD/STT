"""Tests for text delivery into the focused window.

The real SendInput/clipboard calls are patched out; these tests verify routing
decisions (typed vs pasted) and the call sequence, not Windows itself.
"""

from __future__ import annotations

import numpy as np
import pytest

from stt import audio as audio_mod
from stt import typing
from stt.audio import Recorder
from stt.typing import InjectionBlocked


class _FakeStream:
    """Stands in for sounddevice.RawInputStream so no device is opened."""

    created: list["_FakeStream"] = []

    def __init__(self, **kwargs):
        self.callback = kwargs.get("callback")
        self.started = False
        self.stopped = False
        self.closed = False
        _FakeStream.created.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


@pytest.fixture
def fake_device(monkeypatch):
    _FakeStream.created.clear()
    monkeypatch.setattr(audio_mod.sd, "RawInputStream", _FakeStream)
    return _FakeStream


def _feed(recorder, blocks):
    block = np.zeros(1024, dtype=np.int16)
    for _ in range(blocks):
        recorder._callback(block, 1024, None, None)


# ------------------------------------------------------- max_seconds handling
def test_reaching_the_limit_keeps_the_recorder_alive(fake_device):
    """Hitting max_seconds must not orphan the stream.

    The callback used to clear `_active` here. Nothing else owns the stream, so
    it was never closed and the App's max timer keys off is_recording, so it
    never even ran: the take was dropped and the device stayed open.
    """
    rec = Recorder(sample_rate=16000, max_seconds=1)
    rec.start()
    _feed(rec, 20)  # 20480 frames against a 16000 frame cap
    assert rec.is_recording is True
    assert rec.truncated is True


def test_reaching_the_limit_keeps_the_audio(fake_device):
    rec = Recorder(sample_rate=16000, max_seconds=1)
    rec.start()
    _feed(rec, 20)
    pcm = rec.stop()
    assert pcm.size == 20 * 1024
    assert pcm.dtype == np.int16


def test_reaching_the_limit_closes_the_stream(fake_device):
    rec = Recorder(sample_rate=16000, max_seconds=1)
    rec.start()
    stream = _FakeStream.created[-1]
    _feed(rec, 20)
    rec.stop()
    assert stream.stopped and stream.closed


def test_a_take_under_the_limit_is_not_flagged(fake_device):
    rec = Recorder(sample_rate=16000, max_seconds=10)
    rec.start()
    _feed(rec, 5)
    assert rec.is_recording and rec.truncated is False
    rec.stop()
    assert rec.truncated is False


def test_the_memory_backstop_freezes_growth_without_disowning_the_stream(fake_device):
    """A wedged timer must cost memory, not leak a device."""
    rec = Recorder(sample_rate=16000, max_seconds=1)
    rec.start()
    stream = _FakeStream.created[-1]
    _feed(rec, 1000)
    assert rec._frames >= rec._frames_cap
    frozen_at = rec._frames
    _feed(rec, 100)
    assert rec._frames == frozen_at  # no further accumulation
    assert rec.is_recording is True  # stop() still runs, still frees the device
    rec.stop()
    assert stream.closed


def test_restarting_clears_the_truncated_flag(fake_device):
    rec = Recorder(sample_rate=16000, max_seconds=1)
    rec.start()
    _feed(rec, 20)
    rec.stop()
    rec.start()
    assert rec.truncated is False
    rec.stop()


def test_two_takes_do_not_stack_streams(fake_device):
    for _ in range(3):
        rec = Recorder(sample_rate=16000, max_seconds=1)
        rec.start()
        _feed(rec, 20)
        rec.stop()
    assert all(s.closed for s in _FakeStream.created)
    assert len(_FakeStream.created) == 3


# ------------------------------------------------------- injected keystrokes
def _no_send_input(monkeypatch, result):
    monkeypatch.setattr(typing.user32, "SendInput", lambda *a: result)


def test_a_rejected_keystroke_is_reported_not_swallowed(monkeypatch):
    """SendInput returns 0 when UIPI refuses it, which used to look typed."""
    _no_send_input(monkeypatch, 0)
    with pytest.raises(InjectionBlocked):
        typing.type_text("hi", delay_ms=0)


def test_accepted_keystrokes_type_normally(monkeypatch):
    _no_send_input(monkeypatch, 1)
    typing.type_text("hi", delay_ms=0)  # must not raise


def test_one_rejected_keystroke_stops_the_run(monkeypatch):
    """No point typing 400 characters into a window that refused the first."""
    calls = []

    def send(*a):
        calls.append(1)
        return 1 if len(calls) < 3 else 0

    monkeypatch.setattr(typing.user32, "SendInput", send)
    with pytest.raises(InjectionBlocked):
        typing.type_text("abcdefghij", delay_ms=0)
    assert len(calls) == 3


def test_newline_and_tab_do_not_go_through_unicode(monkeypatch):
    """VK_RETURN and VK_TAB report nothing, so they must not raise.

    keybd_event gives no way to learn whether the keystroke landed, so these two
    stay best effort; only the Unicode path can be verified.
    """
    monkeypatch.setattr(typing.user32, "SendInput", lambda *a: 0)
    monkeypatch.setattr(typing.user32, "keybd_event", lambda *a: 0)
    typing.type_text("\n\t", delay_ms=0)  # must not raise


def test_injection_blocked_is_a_runtime_error():
    assert issubclass(InjectionBlocked, RuntimeError)


@pytest.fixture
def spy(monkeypatch):
    calls = {"typed": [], "pasted": [], "clipboard": []}
    monkeypatch.setattr(
        typing, "type_text", lambda text, delay_ms=0: calls["typed"].append((text, delay_ms))
    )
    monkeypatch.setattr(
        typing,
        "paste_text",
        lambda text, restore_clipboard=True: (
            calls["pasted"].append((text, restore_clipboard)) or True
        ),
    )
    monkeypatch.setattr(
        typing, "_clipboard_set", lambda text: calls["clipboard"].append(text) or True
    )
    return calls


def test_short_text_is_typed(spy):
    method = typing.deliver("hello", delay_ms=5, clipboard_backup=True)
    assert method == "typed"
    assert spy["typed"] == [("hello", 5)]
    assert spy["pasted"] == []


def test_short_text_without_backup_leaves_clipboard_alone(spy):
    typing.deliver("hello", clipboard_backup=False)
    assert spy["clipboard"] == []


def test_short_text_with_backup_copies(spy):
    typing.deliver("hello", clipboard_backup=True)
    assert spy["clipboard"] == ["hello"]


def test_long_text_switches_to_paste(spy):
    long_text = "word " * 100
    method = typing.deliver(long_text, clipboard_backup=True)
    assert method == "pasted"
    assert spy["typed"] == []
    assert spy["pasted"] == [(long_text, False)]


def test_empty_text_is_harmless(spy):
    assert typing.deliver("") == "typed"
    assert spy["typed"] == [("", 6)]


def test_clear_clipboard_empties_and_closes(monkeypatch):
    calls = []
    monkeypatch.setattr(
        typing.user32, "OpenClipboard", lambda owner: calls.append("open") or 1
    )
    monkeypatch.setattr(
        typing.user32, "EmptyClipboard", lambda: calls.append("empty") or 1
    )
    monkeypatch.setattr(
        typing.user32, "CloseClipboard", lambda: calls.append("close") or 1
    )
    assert typing.clear_clipboard() is True
    assert calls == ["open", "empty", "close"]


def test_clear_clipboard_reports_failure_when_locked(monkeypatch):
    monkeypatch.setattr(typing.user32, "OpenClipboard", lambda owner: 0)
    assert typing.clear_clipboard() is False


# ------------------------------------------------------------------ recorder
def test_rms_of_silence_is_zero():
    assert Recorder._rms(np.zeros(1024, dtype=np.int16)) == 0.0


def test_rms_of_loud_signal_is_large():
    loud = np.full(1024, 20000, dtype=np.int16)
    assert Recorder._rms(loud) > 0.5


def test_rms_of_empty_block_is_zero():
    assert Recorder._rms(np.zeros(0, dtype=np.int16)) == 0.0


def test_level_is_clamped_to_one(monkeypatch):
    rec = Recorder()
    for _ in range(50):
        rec._levels.append(5.0)
    assert rec.level() == 1.0


def test_rms_ignores_odd_length_blocks():
    # A non-multiple-of-2 buffer must not raise (float16 promotion pitfalls).
    assert Recorder._rms(np.ones(1001, dtype=np.int16)) > 0.0
