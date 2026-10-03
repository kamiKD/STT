"""Native window details: the type face, the shared fill, and capabilities.

The surfaces are near-opaque pills over per-pixel transparent windows, so no
compositor backdrop is involved: `fill` below is the whole material story.
What remains of the platform here is the font stack (Segoe UI Variable, the
closest relative Windows has to SF Pro), the reduced-motion accessibility flag,
and capability probing for `python -m stt --check`.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

# The one fill both surfaces use. A single constant so the pill and the dialog
# cannot drift apart: near-opaque black, so white text holds without hiding
# the soft shadow behind it.
PILL_FILL = "rgba(8, 8, 10, 235)"

# "Turn off animations" in the Windows accessibility settings. Honouring it is
# not decoration: users who set it want it respected.
SPI_GETCLIENTAREAANIMATION = 0x1042


def _build_number() -> int:
    import sys

    return sys.getwindowsversion().build


HAVE_DWM = True
# 22000 is Windows 11.
HAVE_SYSTEM_BACKDROP = _build_number() >= 22000
# 19041 is Windows 10 2004.
HAVE_ACRYLIC_ACCENT = _build_number() >= 19041


_reduced_motion: bool | None = None


def reduced_motion() -> bool:
    """True when Windows is set to turn off animations.

    Animation does not merely get faster when this is set: it collapses to an
    instant state change, because that is what the setting is asking for.
    """
    global _reduced_motion
    if _reduced_motion is None:
        value = wintypes.DWORD()
        try:
            ok = user32.SystemParametersInfoW(
                SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(value), 0
            )
            _reduced_motion = bool(value.value) if ok else False
        except (OSError, AttributeError):  # pragma: no cover - non-Windows
            _reduced_motion = False
    return _reduced_motion


# Segoe UI Variable is Windows' own variable-weight text face and the closest
# relative the platform has to SF Pro: optical sizing and a real weight axis,
# rather than one bold font pretending to be two. The Display cut is for large
# text, the Text cut for anything read in a sentence.
_FONT_STACK = {
    True: ("Segoe UI Variable Display", "Segoe UI"),
    False: ("Segoe UI Variable Text", "Segoe UI"),
}


def font_family(display: bool = False) -> str:
    """The best available text face, as a name.

    Surfaces style through QSS, which needs the family as a string rather than
    a QFont - and getting this wrong is not cosmetic: metrics computed against
    the fallback face wrap and elide at the wrong width.
    """
    from PySide6.QtGui import QFont

    stack = _FONT_STACK[bool(display)]
    for family in stack:
        font = QFont()
        font.setFamily(family)
        if font.exactMatch():
            return family
    return stack[-1]


def system_font(size: float, weight, display: bool = False):
    """The platform text face at `size` and `weight`, for non-QSS surfaces."""
    from PySide6.QtGui import QFont

    font = QFont(font_family(display))
    font.setPointSizeF(size)
    font.setWeight(weight)
    return font


def describe() -> dict:
    """What this machine supports. Used by ``python -m stt --check``."""
    return {
        "os_build": _build_number(),
        "system_backdrop": HAVE_SYSTEM_BACKDROP,
        "acrylic_accent": HAVE_ACRYLIC_ACCENT,
        "rounded_corners": HAVE_SYSTEM_BACKDROP,
        "reduced_motion": reduced_motion(),
    }