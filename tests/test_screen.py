"""Tests for the PNG encoder.

The encoder is pure, so it can be checked without a screen: the pixel data is
the only part that needs the desktop.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from stt import screen


def _chunks(data: bytes) -> list[tuple[str, bytes]]:
    out = []
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8].decode()
        out.append((kind, data[offset + 8 : offset + 8 + length]))
        offset += 12 + length
    return out


def test_encode_png_starts_with_the_signature():
    assert screen.encode_png(1, 1, b"\x00\x00\x00\x00").startswith(b"\x89PNG\r\n\x1a\n")


def test_encode_png_emits_the_expected_chunks():
    kinds = [kind for kind, _ in _chunks(screen.encode_png(2, 2, bytes(16)))]
    assert kinds == ["IHDR", "IDAT", "IEND"]


def test_ihdr_declares_8_bit_truecolour():
    ihdr = dict(_chunks(screen.encode_png(3, 2, bytes(24))))["IHDR"]
    width, height, depth, colour = struct.unpack(">IIBB", ihdr[:10])
    assert (width, height, depth, colour) == (3, 2, 8, 2)


def test_every_chunk_crc_is_valid():
    data = screen.encode_png(4, 3, bytes(4 * 3 * 4))
    offset = 8
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        payload = data[offset + 8 : offset + 8 + length]
        (crc,) = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])
        assert crc == (zlib.crc32(kind + payload) & 0xFFFFFFFF)
        offset += 12 + length


def test_scanlines_carry_a_filter_byte_each():
    """Each row is one filter byte plus width*3 colour bytes."""
    width, height = 4, 3
    data = screen.encode_png(width, height, bytes(width * height * 4))
    idat = b"".join(p for k, p in _chunks(data) if k == "IDAT")
    raw = zlib.decompress(idat)
    assert len(raw) == height * (width * 3 + 1)
    assert all(raw[row * (width * 3 + 1)] == 0 for row in range(height))


def test_bgra_is_reordered_to_rgb():
    """One pixel of pure red in BGRA must come out as RGB red."""
    data = screen.encode_png(1, 1, bytes((0, 0, 255, 255)))  # B=0 G=0 R=255
    idat = b"".join(p for k, p in _chunks(data) if k == "IDAT")
    raw = zlib.decompress(idat)
    assert tuple(raw[1:4]) == (255, 0, 0)


def test_screenshot_dir_is_under_the_user_pictures_folder():
    assert screen.screenshot_dir().name == "Screenshots"
    assert screen.screenshot_dir().parent.name == "Pictures"


def test_save_screenshot_writes_a_timestamped_file(monkeypatch, tmp_path):
    monkeypatch.setattr(screen, "screenshot_dir", lambda: tmp_path / "shots")
    monkeypatch.setattr(screen, "grab_screen", lambda: (2, 2, bytes(16)))
    path = screen.save_screenshot()
    assert path.exists()
    assert path.parent == tmp_path / "shots"
    assert path.name.startswith("stt-") and path.suffix == ".png"


def test_a_missing_screen_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(screen.user32, "GetSystemMetrics", lambda index: 0)
    with pytest.raises(OSError, match="no screen"):
        screen.grab_screen()