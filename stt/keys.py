"""Hotkey name normalization and combo parsing.

Kept dependency-free so config, CLI and tests can use it without pulling in
the Windows-only keyboard hook library.
"""

from __future__ import annotations

# Aliases -> canonical modifier names. Everything on the right side is what
# the hotkey matcher compares against.
MOD_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "lctrl": "ctrl",
    "rctrl": "ctrl",
    "alt": "alt",
    "alt_l": "alt",
    "alt_r": "alt",
    "alt_gr": "alt",
    "altgr": "alt",
    "shift": "shift",
    "shift_l": "shift",
    "shift_r": "shift",
    "cmd": "win",
    "win": "win",
    "super": "win",
    "meta": "win",
    "windows": "win",
}

# Canonical display names.
MOD_ORDER = ("ctrl", "alt", "shift", "win")
MOD_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}

# Keys we accept as the "trigger" (non-modifier) part of a combo.
TRIGGER_KEYS = {
    "space": "space",
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "backspace": "backspace",
    "esc": "esc",
    "escape": "esc",
    "f1": "f1",
    "f2": "f2",
    "f3": "f3",
    "f4": "f4",
    "f5": "f5",
    "f6": "f6",
    "f7": "f7",
    "f8": "f8",
    "f9": "f9",
    "f10": "f10",
    "f11": "f11",
    "f12": "f12",
    "scroll_lock": "scroll_lock",
    "pause": "pause",
    "insert": "insert",
    "`": "`",
    "caps_lock": "caps_lock",
}


def normalize(name: str) -> str:
    """Canonicalize a raw key name from the OS-level event source."""
    if not name:
        return ""
    key = str(name).strip().lower()
    if key in MOD_ALIASES:
        return MOD_ALIASES[key]
    if key in TRIGGER_KEYS:
        return TRIGGER_KEYS[key]
    return key


# Virtual-key code -> trigger token. Needed because Windows reports an
# Alt-modified letter key (Ctrl+Alt+O) with a garbage `char` and no `name`,
# leaving the virtual key code as the only way to identify the key. It also
# covers layouts where AltGr turns the letter into another character.
VK_ALIASES = {
    0x08: "backspace",
    0x09: "tab",
    0x0D: "enter",
    0x13: "pause",
    0x14: "caps_lock",
    0x1B: "esc",
    0x20: "space",
    0x2D: "insert",
    0x91: "scroll_lock",
    0xC0: "`",
    **{0x30 + i: str(i) for i in range(10)},
    **{0x41 + i: chr(0x61 + i) for i in range(26)},
    **{0x6F + i: f"f{i}" for i in range(1, 13)},
}


def token_for_vk(vk) -> str:
    """Virtual key code -> trigger token, or '' when we do not care."""
    if not vk:
        return ""
    return VK_ALIASES.get(int(vk), "")


def parse_combo(text: str) -> list[str]:
    """Parse 'ctrl+alt+space' -> ['ctrl', 'alt', 'space'].

    Raises ValueError when no trigger key is present or when the same
    modifier is repeated.
    """
    parts = [p for p in str(text).replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")

    mods: list[str] = []
    trigger = ""
    for part in parts:
        token = normalize(part)
        if token in MOD_ORDER:
            if token in mods:
                raise ValueError(f"repeated modifier: {token}")
            mods.append(token)
        else:
            if trigger:
                raise ValueError("only one non-modifier key allowed")
            trigger = token
    if not trigger:
        raise ValueError("hotkey needs a non-modifier key (e.g. space)")
    # Canonical modifier order keeps comparisons stable.
    mods.sort(key=MOD_ORDER.index)
    return mods + [trigger]


def combo_label(combo) -> str:
    """Human readable label, e.g. 'Ctrl+Alt+Space'."""
    labels = []
    for token in combo:
        if token in MOD_LABELS:
            labels.append(MOD_LABELS[token])
        elif token == "space":
            labels.append("Space")
        else:
            labels.append(str(token).upper())
    return "+".join(labels)


def modifiers_of(combo) -> set[str]:
    return {t for t in combo if t in MOD_ORDER}


def trigger_of(combo) -> str:
    return combo[-1]
