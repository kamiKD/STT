"""End-to-end check that SendInput/paste text reaches a real foreground window.

The probe is a plain Win32 window hosting a multiline EDIT control, so both
delivery paths can be observed the way a real app would see them:

  * short text  -> per-character SendInput (WM_CHAR into the edit)
  * long text   -> clipboard + Ctrl+V (edit control handles the paste)

Run it detached from a console, otherwise the console keeps stealing focus and
keystrokes land in the wrong window:

    Start-Process -WindowStyle Hidden python -ArgumentList tools\\verify_typing.py

Prints PASS/FAIL and writes the report to REPORT_PATH.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stt import typing  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t

WM_CHAR = 0x0102
WM_DESTROY = 0x0002
WM_SETTEXT = 0x000C
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
ES_MULTILINE = 0x0004
ES_AUTOVSCROLL = 0x0040
CW_USEDEFAULT = 0x80000000
SW_SHOW = 5

REPORT_PATH = (
    Path(os.environ.get("TEMP", r"C:\Users\user\AppData\Local\Temp"))
    / "opencode"
    / "typing_verify.txt"
)

SAMPLE = "Hello, world! 12345 accents: aeiou \u00e7edilla \u00f1-tilde."
LONG_SAMPLE = "word " * 120


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.WINFUNCTYPE(
            LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        )),
        ("cbClsExtra", wintypes.INT),
        ("cbWndExtra", wintypes.INT),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


def _setup_prototypes() -> None:
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    user32.DefWindowProcW.restype = LRESULT
    user32.DefWindowProcW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]

    user32.RegisterClassW.restype = wintypes.ATOM
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]

    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]

    user32.SendMessageW.restype = LRESULT
    user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]

    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    user32.SetFocus.restype = wintypes.HWND
    user32.SetFocus.argtypes = [wintypes.HWND]

    # SendMessageW is variadic; a string lparam does not marshal through
    # argtypes, so clearing the edit uses SetWindowTextW instead.
    user32.SetWindowTextW.restype = ctypes.c_bool
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]

    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def force_foreground(hwnd: int) -> bool:
    """Bring hwnd to the front despite the foreground-lock rules."""
    if user32.GetForegroundWindow() == hwnd:
        return True
    current_tid = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    our_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached = bool(user32.AttachThreadInput(current_tid, our_tid, True))
    # A tap on ALT satisfies the SetForegroundWindow restrictions.
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 0x0002, 0)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(current_tid, our_tid, False)
    time.sleep(0.2)
    if user32.GetForegroundWindow() == hwnd:
        return True
    try:  # SwitchToThisWindow ignores the foreground lock entirely
        user32.SwitchToThisWindow(hwnd, True)
    except AttributeError:
        pass
    time.sleep(0.2)
    return user32.GetForegroundWindow() == hwnd


def pump(seconds: float) -> None:
    """Drain the message queue so the edit control sees the injected input."""
    msg = wintypes.MSG()
    deadline = time.time() + seconds
    while time.time() < deadline:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def read_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def main() -> int:
    _setup_prototypes()

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc = ctypes.WINFUNCTYPE(
        LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )(wndproc)

    wndclass = WNDCLASS()
    wndclass.lpfnWndProc = proc
    wndclass.hInstance = kernel32.GetModuleHandleW(None)
    wndclass.lpszClassName = "SttVerifyProbe"
    user32.RegisterClassW(ctypes.byref(wndclass))

    hwnd = user32.CreateWindowExW(
        0, "SttVerifyProbe", "stt verify probe",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 700, 300,
        None, None, wndclass.hInstance, None,
    )
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())

    edit = user32.CreateWindowExW(
        0, "EDIT", "",
        WS_CHILD | WS_VISIBLE | ES_MULTILINE | ES_AUTOVSCROLL,
        10, 10, 660, 240,
        hwnd, None, wndclass.hInstance, None,
    )
    if not edit:
        raise ctypes.WinError(ctypes.get_last_error())

    user32.ShowWindow(hwnd, SW_SHOW)
    force_foreground(hwnd)
    pump(0.3)

    lines = [
        f"probe hwnd   : {hwnd}",
        f"edit hwnd    : {edit}",
    ]

    results = {}
    cases = [
        ("short-typed", SAMPLE),        # -> per-character SendInput
        ("long-pasted", LONG_SAMPLE),   # -> clipboard + Ctrl+V
        ("unicode", "\u00e7\u00f1\u00e3\u4f60\u597d"),  # accents + CJK
    ]
    for label, payload in cases:
        match = False
        got = ""
        method = "n/a"
        for _attempt in range(4):
            if not force_foreground(hwnd):
                time.sleep(0.4)
                continue
            user32.SetFocus(edit)
            user32.SetWindowTextW(edit, "")
            time.sleep(0.15)
            method = typing.deliver(payload, delay_ms=4, clipboard_backup=True)
            pump(1.0)
            got = read_text(edit)
            if got.strip() == payload.strip():
                match = True
                break
        results[label] = (method, got, match)
        lines.append(f"[{label}] method  : {method}")
        lines.append(f"[{label}] sent    : {shorten(payload)!r}")
        lines.append(f"[{label}] got     : {shorten(got)!r}")
        lines.append(f"[{label}] match   : {match}")

    ok = all(r[2] for r in results.values())
    lines.append(f"RESULT       : {'PASS' if ok else 'FAIL'}")

    report = "\n".join(lines)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    # The console codepage may not hold CJK/accented output, so the file is
    # the authoritative report.
    print(report.encode("ascii", "backslashreplace").decode("ascii"))

    user32.DestroyWindow(hwnd)
    return 0 if ok else 1


def shorten(text: str, limit: int = 60) -> str:
    return text if len(text) <= limit else text[:limit] + f"...(+{len(text) - limit})"


if __name__ == "__main__":
    raise SystemExit(main())
