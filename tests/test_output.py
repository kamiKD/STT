"""Tests for text delivery into the focused window.

The real SendInput/clipboard calls are patched out; these tests verify routing
decisions (typed vs pasted) and the call sequence, not Windows itself.
"""

from __future__ import annotations

import numpy as np
import pytest

from stt import typing
from stt.audio import Recorder


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
