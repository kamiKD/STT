"""Deliver text into whatever window currently has focus.

Two paths, both hitting the focused app rather than our own:
  1. SendInput with KEYEVENTF_UNICODE - types any Unicode char (accents,
     emoji, CJK) without touching the clipboard.
  2. Clipboard + Ctrl+V - fallback for very long text or apps that drop
     synthetic key events (some Electron/remote-desktop cases).

The clipboard is snapshotted first and restored afterwards, so a paste fallback
does not destroy what the user had copied.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
VK_CONTROL = 0x11
VK_V = 0x56

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else wintypes.DWORD


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


# Clipboard
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _clipboard_get() -> str | None:
    try:
        if not user32.OpenClipboard(None):
            return None
        try:
            if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                return None
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GlobalLock.restype = ctypes.c_void_p
            ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
            if not ptr:
                return None
            try:
                return ctypes.c_wchar_p(ptr).value
            finally:
                kernel32.GlobalUnlock(ctypes.c_void_p(handle))
        finally:
            user32.CloseClipboard()
    except Exception:
        return None


def _clipboard_set(text: str) -> bool:
    try:
        if not user32.OpenClipboard(None):
            return False
        try:
            user32.EmptyClipboard()
            data = text + "\0"
            size = len(data) * ctypes.sizeof(ctypes.c_wchar)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.GlobalAlloc.restype = ctypes.c_void_p
            kernel32.GlobalLock.restype = ctypes.c_void_p
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                return False
            ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
            if not ptr:
                return False
            try:
                ctypes.memmove(ptr, ctypes.create_unicode_buffer(data), size)
            finally:
                kernel32.GlobalUnlock(ctypes.c_void_p(handle))
            user32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(handle))
            return True
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def _send_vk(vk: int, up: bool = False) -> None:
    flags = KEYEVENTF_KEYUP if up else 0
    user32.keybd_event(vk, 0, flags, 0)


def _send_unicode_char(ch: str) -> bool:
    code = ord(ch)
    if code > 0xFFFF:  # surrogate pair
        code -= 0x10000
        units = (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))
    else:
        units = (code,)
    for unit in units:
        for is_up in (False, True):
            inp = _INPUT()
            inp.type = INPUT_KEYBOARD
            inp.ki = _KEYBDINPUT(0, unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if is_up else 0), 0, 0)
            if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
                return False
    return True


class InjectionBlocked(RuntimeError):
    """SendInput was refused, so nothing reached the focused window.

    Almost always the UIPI boundary: the target window runs elevated and this
    process does not. Reporting it beats telling the user a transcript was
    inserted when the window never saw a key.
    """


def foreground_window() -> int:
    return user32.GetForegroundWindow()


def focused_window() -> int:
    return user32.GetFocus()


def type_text(text: str, delay_ms: int = 6) -> None:
    """Type text into the focused window using synthetic Unicode keystrokes.

    Raises InjectionBlocked when Windows refuses the input, instead of
    reporting a dictation that never landed.
    """
    delay = max(0, delay_ms) / 1000.0
    for ch in text:
        if ch == "\n":
            _send_vk(0x0D)  # VK_RETURN types better than a raw \n in most apps
        elif ch == "\t":
            _send_vk(0x09)
        elif not _send_unicode_char(ch):
            raise InjectionBlocked(
                "SendInput was rejected by the focused window (UIPI)"
            )
        if delay:
            time.sleep(delay)


def _send_paste() -> None:
    time.sleep(0.02)
    _send_vk(VK_CONTROL)
    _send_vk(VK_V)
    _send_vk(VK_V, up=True)
    _send_vk(VK_CONTROL, up=True)


def paste_text(text: str, restore_clipboard: bool = True) -> bool:
    """Put text on the clipboard and send Ctrl+V to the focused window.

    With `restore_clipboard` the previous clipboard content is put back, so the
    paste is invisible to the user's clipboard history.
    """
    saved = _clipboard_get() if restore_clipboard else None
    if not _clipboard_set(text):
        return False
    _send_paste()
    if saved is not None:
        time.sleep(0.1)
        _clipboard_set(saved)
    return True


def copy_to_clipboard(text: str) -> bool:
    return _clipboard_set(text)


def clear_clipboard() -> bool:
    """Empty the clipboard without disturbing the window that owns it."""
    try:
        if not user32.OpenClipboard(None):
            return False
        try:
            return bool(user32.EmptyClipboard())
        finally:
            user32.CloseClipboard()
    except Exception:
        return False


def deliver(text: str, delay_ms: int = 6, clipboard_backup: bool = True, paste_threshold: int = 200) -> str:
    """Put `text` into the focused app. Returns a short description of the method.

    Long text goes through the clipboard because per-character SendInput is
    slow and can be dropped by heavier apps; anything short is typed directly so
    the clipboard is untouched.
    """
    if len(text) >= paste_threshold:
        # Long text: paste without restoring, so the transcript also stays on
        # the clipboard as a backup.
        return "pasted" if paste_text(text, restore_clipboard=False) else "copied"

    if clipboard_backup:
        _clipboard_set(text)
    type_text(text, delay_ms=delay_ms)
    return "typed"
