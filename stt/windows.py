"""Top-level window lookup and control through user32.

Closing an app by voice posts WM_CLOSE to the windows whose title matches,
which is exactly what the X button does: the app still gets to ask about
unsaved work. Nothing here force-kills a process.

Handle widths matter: without explicit argtypes ctypes truncates HWNDs to
32 bits, which silently loses every window on a 64-bit build.
"""

from __future__ import annotations

import ctypes
import difflib
import os
from ctypes import wintypes
from dataclasses import dataclass

from .text import normalize

user32 = ctypes.WinDLL("user32", use_last_error=True)

WM_CLOSE = 0x0010
WM_APPCOMMAND = 0x0319
WM_SYSCOMMAND = 0x0112
SC_MINIMIZEALL = 0xF020

APPCOMMAND_VOLUME_MUTE = 0xAD00
APPCOMMAND_VOLUME_DOWN = 0xAE00
APPCOMMAND_VOLUME_UP = 0xAF00

MATCH_MIN_SCORE = 0.7

_EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [_EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ulonglong


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    pid: int = 0


def list_windows(visible_only: bool = True) -> list[WindowInfo]:
    """Every top-level window that has a title."""
    found: list[WindowInfo] = []

    def collect(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        title = buffer.value.strip()
        if not title:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(WindowInfo(int(hwnd), title, int(pid.value)))
        return True

    user32.EnumWindows(_EnumWindowsProc(collect), 0)
    return found


def match_windows(
    query: str,
    *,
    windows: list[WindowInfo] | None = None,
    exclude_pid: int | None = None,
    min_score: float = MATCH_MIN_SCORE,
) -> list[WindowInfo]:
    """Windows whose title matches `query`, best first.

    `exclude_pid` drops our own windows, so a fuzzy hit on the overlay's title
    can never make the app close itself.
    """
    target = normalize(query)
    if not target:
        return []
    if exclude_pid is None:
        exclude_pid = os.getpid()

    pool = list_windows() if windows is None else list(windows)
    pool = [w for w in pool if w.pid != exclude_pid and normalize(w.title)]

    for window in pool:
        if normalize(window.title) == target:
            return [window]

    prefix = sorted(
        (w for w in pool if normalize(w.title).startswith(target)), key=lambda w: len(w.title)
    )
    if prefix:
        return prefix

    contained = sorted(
        (w for w in pool if target in normalize(w.title).split()), key=lambda w: len(w.title)
    )
    if contained:
        return contained

    keys = {id(w): normalize(w.title) for w in pool}
    close = difflib.get_close_matches(target, list(keys.values()), n=3, cutoff=min_score)
    if not close:
        return []
    return [w for w in pool if keys[id(w)] in close]


def window_titles(windows: list[WindowInfo] | None = None) -> list[str]:
    return [w.title for w in (list_windows() if windows is None else windows)]


def close_window(hwnd: int) -> bool:
    """Ask a window to close, the same way its title-bar X would."""
    return bool(user32.PostMessageW(wintypes.HWND(hwnd), WM_CLOSE, 0, 0))


def close_windows(handles) -> int:
    return sum(1 for hwnd in handles if close_window(hwnd))


def broadcast(command: int) -> None:
    """Send WM_APPCOMMAND to every top-level window.

    Volume keys behave like a media keyboard: the command goes to whatever is
    focused, and a broadcast is how that reaches apps that ignore the virtual
    key codes.
    """
    for window in list_windows(visible_only=False):
        user32.SendMessageW(
            wintypes.HWND(window.hwnd), WM_APPCOMMAND, 0, ctypes.c_long(command)
        )


def show_desktop() -> None:
    for window in list_windows():
        user32.SendMessageW(wintypes.HWND(window.hwnd), WM_SYSCOMMAND, SC_MINIMIZEALL, 0)
        break