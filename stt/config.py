"""Persistent configuration (JSON in %APPDATA%\\Speech-To-Text)."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from . import keys

APP_DIR_NAME = "Speech-To-Text"
CONFIG_FILENAME = "config.json"
KEY_FILENAME = "groq_key.txt"

MODELS = ("whisper-large-v3", "whisper-large-v3-turbo")

# Tried in order for the voice-command hotkey until one differs from the
# dictation hotkey. Sharing a combo would fire both listeners per press.
COMMAND_HOTKEY_FALLBACKS = (
    ["ctrl", "alt", "o"],
    ["ctrl", "shift", "o"],
    ["ctrl", "alt", "j"],
    ["win", "alt", "o"],
    ["ctrl", "alt", "space"],
)

# Keys this app used to write, and what they became. A config saved before the
# rename keeps working: the old value is copied across once and the file is
# rewritten with the new names.
LEGACY_KEYS = {
    "open_hotkey": "command_hotkey",
    "open_with_voice": "command_with_voice",
    "open_aliases": "command_aliases",
}

# Languages offered in the tray menu. "auto" lets the model decide.
LANGUAGES = {
    "auto": "Auto-detect",
    "en": "English",
    "pt": "Portuguese",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "nl": "Dutch",
    "ru": "Russian",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "tr": "Turkish",
    "pl": "Polish",
}

DEFAULTS = {
    # Input
    "hotkey": ["ctrl", "alt", "space"],
    # Voice commands ("Command with Voice")
    "command_hotkey": COMMAND_HOTKEY_FALLBACKS[0],
    "command_with_voice": True,
    "command_system_enabled": True,
    "command_aliases": {},
    # Transcription
    "model": "whisper-large-v3",
    "language": "en",
    "prompt": "",
    "max_seconds": 120,
    "min_rms": 0.0035,
    "trailing_space": True,
    # Output
    "auto_type": True,
    "clipboard_backup": True,
    "clipboard_only": False,
    "type_delay_ms": 6,
    # UI / system
    "overlay_enabled": True,
    "start_with_windows": False,
    "sample_rate": 16000,
}


def app_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_DIR_NAME


def config_path() -> Path:
    return app_dir() / CONFIG_FILENAME


def key_path() -> Path:
    return app_dir() / KEY_FILENAME


class Config(dict):
    """dict with attribute access; unknown keys fall back to DEFAULTS."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value

    def save(self) -> None:
        save_config(self)

    def copy_config(self) -> "Config":
        return Config(dict(self))


def _coerce(cfg: dict) -> Config:
    """Repair unknown/invalid values coming from an older or hand-edited file."""
    out = Config(DEFAULTS)
    for key, value in cfg.items():
        if key not in DEFAULTS:
            continue
        default = DEFAULTS[key]
        try:
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = int(value)
            elif isinstance(default, float):
                value = float(value)
            elif isinstance(default, list):
                value = list(value) if isinstance(value, (list, tuple)) else default
            elif isinstance(default, dict):
                value = (
                    {str(k): str(v) for k, v in value.items()}
                    if isinstance(value, dict)
                    else default
                )
            elif isinstance(default, str):
                value = str(value)
        except (TypeError, ValueError):
            value = default
        out[key] = value

    try:
        out["hotkey"] = keys.parse_combo("+".join(out["hotkey"]))
    except (ValueError, TypeError):
        out["hotkey"] = list(DEFAULTS["hotkey"])
    try:
        out["command_hotkey"] = keys.parse_combo("+".join(out["command_hotkey"]))
    except (ValueError, TypeError, AttributeError):
        out["command_hotkey"] = list(DEFAULTS["command_hotkey"])
    if out["command_hotkey"] == out["hotkey"]:
        out["command_hotkey"] = next(
            (list(combo) for combo in COMMAND_HOTKEY_FALLBACKS if combo != out["hotkey"]),
            list(DEFAULTS["hotkey"]),
        )
    if out["model"] not in MODELS:
        out["model"] = DEFAULTS["model"]
    if out["language"] not in LANGUAGES:
        out["language"] = DEFAULTS["language"]
    out["max_seconds"] = max(5, min(600, out["max_seconds"]))
    out["type_delay_ms"] = max(0, min(100, out["type_delay_ms"]))
    out["sample_rate"] = int(out["sample_rate"]) if out["sample_rate"] in (16000, 22050, 44100, 48000) else 16000
    return out


_lock = threading.Lock()


def _migrate(raw: dict) -> tuple[dict, bool]:
    """Rename pre-'Command with Voice' keys. Returns (config, did_anything)."""
    out = dict(raw)
    changed = False
    for old, new in LEGACY_KEYS.items():
        if old in out and new not in out:
            out[new] = out.pop(old)
            changed = True
    return out, changed


def load_config() -> Config:
    path = config_path()
    raw = {}
    with _lock:
        if path.exists():
            try:
                # utf-8-sig also accepts plain utf-8, and tolerates the BOM that
                # Windows editors write by default. Without it a hand-edited
                # file would parse as corrupt and silently reset to defaults.
                raw = json.loads(path.read_text(encoding="utf-8-sig")) or {}
            except (OSError, ValueError):
                raw = {}
    if not isinstance(raw, dict):
        raw = {}
    raw, migrated = _migrate(raw)
    cfg = _coerce(raw)
    if migrated:
        # Rewrite once so the old names stop coming back on every start.
        save_config(cfg)
    return cfg


def save_config(cfg: dict) -> bool:
    path = config_path()
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(dict(cfg), indent=2), encoding="utf-8")
            return True
        except OSError:
            return False


def read_api_key() -> str:
    """Groq key from the environment or from the local key file."""
    env = os.environ.get("GROQ_API_KEY", "").strip()
    if env:
        return env
    path = key_path()
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def write_api_key(value: str) -> bool:
    path = key_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value.strip(), encoding="utf-8")
        return True
    except OSError:
        return False


def mask_key(key: str) -> str:
    if not key:
        return "not set"
    tail = key[-4:] if len(key) > 8 else ""
    return f"set ({'...'}{tail})" if tail else "set"
