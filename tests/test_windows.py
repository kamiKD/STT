"""Tests for the Win32 window helpers.

The desktop is real but must not be disturbed, so every test injects a window
list and asserts on the posted messages rather than on what Windows did.
"""

from __future__ import annotations

import ctypes

import pytest

from stt import windows
from stt.windows import WindowInfo


@pytest.fixture
def wins():
    return [
        WindowInfo(10, "Notepad", 100),
        WindowInfo(11, "Spotify Premium", 101),
        WindowInfo(12, "Documents", 102),
        WindowInfo(13, "Downloads", 103),
    ]


def test_match_prefers_an_exact_title(wins):
    assert [w.hwnd for w in windows.match_windows("Notepad", windows=wins)] == [10]


def test_match_ignores_case(wins):
    assert [w.hwnd for w in windows.match_windows("notepad", windows=wins)] == [10]


def test_match_falls_back_to_a_prefix(wins):
    assert [w.hwnd for w in windows.match_windows("Spot", windows=wins)] == [11]


def test_match_falls_back_to_a_word_inside_the_title(wins):
    assert [w.hwnd for w in windows.match_windows("premium", windows=wins)] == [11]


def test_match_tolerates_a_spelling_slip(wins):
    assert [w.hwnd for w in windows.match_windows("notped", windows=wins)] == [10]


def test_match_returns_nothing_for_an_unknown_title(wins):
    assert windows.match_windows("autocad", windows=wins) == []


def test_match_returns_nothing_for_an_empty_query(wins):
    assert windows.match_windows("   ", windows=wins) == []


def test_match_never_returns_our_own_windows():
    ours = WindowInfo(99, "Speech To Text", 777)
    found = windows.match_windows(
        "speech to text", windows=[ours], exclude_pid=777
    )
    assert found == []


def test_list_windows_finds_this_process():
    """The real desktop has windows, so the enum path itself gets exercised."""
    found = windows.list_windows()
    assert isinstance(found, list)
    assert all(isinstance(w.hwnd, int) and w.title for w in found)


def _handle(value):
    """HWND arrives as c_void_p because argtypes declares it as a pointer."""
    return getattr(value, "value", value)


def test_close_window_posts_wm_close(monkeypatch):
    sent = []
    monkeypatch.setattr(
        windows.user32,
        "PostMessageW",
        lambda hwnd, msg, w, l: sent.append((_handle(hwnd), msg)) or 1,
    )
    assert windows.close_window(42) is True
    assert sent == [(42, windows.WM_CLOSE)]


def test_close_windows_counts_the_accepted_posts(monkeypatch):
    monkeypatch.setattr(
        windows.user32, "PostMessageW", lambda hwnd, msg, w, l: 1 if _handle(hwnd) != 11 else 0
    )
    assert windows.close_windows([10, 11, 12]) == 2


def test_broadcast_touches_every_window(monkeypatch):
    posted = []
    monkeypatch.setattr(windows, "list_windows", lambda visible_only=True: wins_or_two())
    monkeypatch.setattr(
        windows.user32,
        "PostMessageW",
        lambda hwnd, msg, w, l: posted.append((_handle(hwnd), msg)),
    )
    windows.broadcast(windows.APPCOMMAND_VOLUME_MUTE)
    assert [hwnd for hwnd, _ in posted] == [10, 11]
    assert {msg for _, msg in posted} == {windows.WM_APPCOMMAND}


def test_broadcast_posts_a_type_lparam_the_prototype_accepts(monkeypatch):
    """The lparam has to survive ctypes' own conversion, not just the stub.

    SendMessage/PostMessage declare LPARAM, which is c_longlong on 64-bit
    builds. Passing a ctypes.c_long there raises ArgumentError, and it did:
    every volume command raised before it reached the stub above.
    """
    monkeypatch.setattr(windows, "list_windows", lambda visible_only=True: wins_or_two())
    argtypes = windows.user32.PostMessageW.argtypes
    for command in (
        windows.APPCOMMAND_VOLUME_MUTE,
        windows.APPCOMMAND_VOLUME_UP,
        windows.APPCOMMAND_VOLUME_DOWN,
    ):
        argtypes[3].from_param(command)  # must not raise


def test_broadcast_does_not_block_on_a_window(monkeypatch):
    """Post, not Send: a hung window must not stall the command."""
    calls = []
    monkeypatch.setattr(windows, "list_windows", lambda visible_only=True: wins_or_two())
    monkeypatch.setattr(
        windows.user32, "PostMessageW", lambda *a: calls.append("post")
    )
    monkeypatch.setattr(
        windows.user32, "SendMessageW", lambda *a: calls.append("send")
    )
    windows.broadcast(windows.APPCOMMAND_VOLUME_UP)
    assert calls == ["post", "post"]
    assert "send" not in calls


def wins_or_two():
    return [WindowInfo(10, "a", 1), WindowInfo(11, "b", 2)]


def test_show_desktop_minimizes_the_foreground_window(monkeypatch):
    sent = []
    monkeypatch.setattr(windows, "list_windows", lambda: wins_or_two())
    monkeypatch.setattr(
        windows.user32,
        "SendMessageW",
        lambda hwnd, msg, w, l: sent.append((_handle(hwnd), msg, w)),
    )
    windows.show_desktop()
    assert sent == [(10, windows.WM_SYSCOMMAND, windows.SC_MINIMIZEALL)]


def test_window_titles_helper(wins):
    assert windows.window_titles(wins) == ["Notepad", "Spotify Premium", "Documents", "Downloads"]


def test_enum_callback_prototype_is_callable():
    """A bad WINFUNCTYPE would blow up inside EnumWindows on a real desktop."""
    assert ctypes.sizeof(windows._EnumWindowsProc) == ctypes.sizeof(ctypes.c_void_p)