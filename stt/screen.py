"""Full-screen capture written straight to PNG, with no image library.

BitBlt into a top-down DIB section, then encode the pixels: a PNG is a header,
a deflated buffer of prefixed scanlines and a CRC per chunk, and zlib ships
with Python. numpy is already a dependency and does the channel shuffle in one
step instead of two million Python iterations.
"""

from __future__ import annotations

import ctypes
import os
import struct
import zlib
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
SM_CXSCREEN = 0
SM_CYSCREEN = 1

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def grab_screen() -> tuple[int, int, bytes]:
    """Copy the primary screen into a buffer as top-down BGRA."""
    width = user32.GetSystemMetrics(SM_CXSCREEN)
    height = user32.GetSystemMetrics(SM_CYSCREEN)
    if width <= 0 or height <= 0:
        raise OSError("no screen reported by Windows")

    header = BITMAPINFO()
    header.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    header.bmiHeader.biWidth = width
    # Negative height asks for a top-down DIB, so rows arrive top to bottom.
    header.bmiHeader.biHeight = -height
    header.bmiHeader.biPlanes = 1
    header.bmiHeader.biBitCount = 32
    header.bmiHeader.biCompression = BI_RGB

    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise OSError("could not open the screen device context")
    memory_dc = bitmap = None
    try:
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        if not memory_dc:
            raise OSError("could not create an offscreen device context")
        raw = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(
            screen_dc,
            ctypes.byref(header),
            DIB_RGB_COLORS,
            ctypes.byref(raw),
            None,
            0,
        )
        if not bitmap or not raw:
            raise OSError("could not allocate the capture buffer")

        previous = gdi32.SelectObject(memory_dc, bitmap)
        try:
            if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, 0, 0, SRCCOPY):
                raise OSError("BitBlt failed")
            size = width * height * 4
            buffer = ctypes.string_at(raw, size)
        finally:
            gdi32.SelectObject(memory_dc, previous)
        return width, height, buffer
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(None, screen_dc)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def encode_png(width: int, height: int, bgra: bytes) -> bytes:
    """Wrap a top-down 32-bit BGRA buffer as an 8-bit truecolour PNG."""
    pixels = np.frombuffer(bgra, dtype=np.uint8).reshape(height, width, 4)
    rgb = pixels[:, :, [2, 1, 0]].reshape(height, width * 3)
    # Every scanline needs a leading filter byte; 0 means "no filter".
    scanlines = np.zeros((height, width * 3 + 1), dtype=np.uint8)
    scanlines[:, 1:] = rgb

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(scanlines.tobytes(), 6))
        + _chunk(b"IEND", b"")
    )


def screenshot_dir() -> Path:
    pictures = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
    return Path(pictures)


def save_screenshot() -> Path:
    """Capture the primary screen into Pictures\\Screenshots and return the path."""
    width, height, buffer = grab_screen()
    target = screenshot_dir() / f"stt-{datetime.now():%Y%m%d-%H%M%S}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encode_png(width, height, buffer))
    return target