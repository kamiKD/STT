"""Unit tests for the parts that do not need a microphone or a GUI."""

from __future__ import annotations

import json

import numpy as np
import pytest

from stt import config as config_mod
from stt import keys as keyutil
from stt import transcribe, typing


# ------------------------------------------------------------------- hotkeys
def test_parse_combo_orders_modifiers():
    assert keyutil.parse_combo("space+alt+ctrl") == ["ctrl", "alt", "space"]


def test_parse_combo_rejects_modifier_only():
    with pytest.raises(ValueError):
        keyutil.parse_combo("ctrl+alt")


def test_parse_combo_rejects_duplicates():
    with pytest.raises(ValueError):
        keyutil.parse_combo("ctrl+ctrl+space")


def test_combo_label_is_human_readable():
    assert keyutil.combo_label(["ctrl", "alt", "space"]) == "Ctrl+Alt+Space"
    assert keyutil.combo_label(["shift", "f2"]) == "Shift+F2"


def test_split_modifiers_and_trigger():
    combo = keyutil.parse_combo("ctrl+alt+space")
    assert keyutil.modifiers_of(combo) == {"ctrl", "alt"}
    assert keyutil.trigger_of(combo) == "space"


@pytest.mark.parametrize(
    "vk,token",
    [(0x4F, "o"), (0x41, "a"), (0x37, "7"), (0x20, "space"), (0x70, "f1"), (0x1B, "esc")],
)
def test_token_for_vk_covers_the_triggers_we_accept(vk, token):
    assert keyutil.token_for_vk(vk) == token


@pytest.mark.parametrize("vk", [None, 0, 0x1234])
def test_token_for_vk_ignores_codes_we_do_not_support(vk):
    assert keyutil.token_for_vk(vk) == ""


# -------------------------------------------------------------------- config
def test_config_repairs_bad_values():
    cfg = config_mod._coerce(
        {"model": "not-a-model", "language": "xx", "hotkey": ["ctrl"], "max_seconds": 99999}
    )
    assert cfg["model"] == config_mod.DEFAULTS["model"]
    assert cfg["language"] == config_mod.DEFAULTS["language"]
    assert cfg["hotkey"] == list(config_mod.DEFAULTS["hotkey"])
    assert cfg["max_seconds"] == 600


def test_config_repairs_a_bad_overlay_position():
    cfg = config_mod._coerce({"overlay_position": "left"})
    assert cfg["overlay_position"] == "bottom"


def test_config_keeps_a_valid_overlay_position():
    cfg = config_mod._coerce({"overlay_position": "top"})
    assert cfg["overlay_position"] == "top"


def test_config_drops_unknown_keys():
    cfg = config_mod._coerce({"nope": 1, "auto_type": False})
    assert "nope" not in cfg
    assert cfg["auto_type"] is False


def test_config_repairs_a_bad_command_hotkey():
    cfg = config_mod._coerce({"command_hotkey": ["ctrl"]})
    assert cfg["command_hotkey"] == list(config_mod.DEFAULTS["command_hotkey"])


def test_config_separates_the_two_hotkeys():
    """Sharing one combo would fire both listeners on a single physical press."""
    cfg = config_mod._coerce({"hotkey": ["ctrl", "alt", "o"]})
    assert cfg["command_hotkey"] != cfg["hotkey"]


def test_config_coerces_aliases_to_string_pairs():
    cfg = config_mod._coerce({"command_aliases": {"browser": "chrome", "editor": 7}})
    assert cfg["command_aliases"] == {"browser": "chrome", "editor": "7"}


def test_config_discards_aliases_that_are_not_a_mapping():
    cfg = config_mod._coerce({"command_aliases": ["browser"]})
    assert cfg["command_aliases"] == {}


def test_legacy_voice_open_keys_are_migrated():
    """A config written before the rename keeps working."""
    migrated, changed = config_mod._migrate(
        {
            "open_hotkey": ["ctrl", "win", "j"],
            "open_with_voice": False,
            "open_aliases": {"browser": "chrome"},
        }
    )
    assert changed is True
    assert migrated == {
        "command_hotkey": ["ctrl", "win", "j"],
        "command_with_voice": False,
        "command_aliases": {"browser": "chrome"},
    }


def test_a_new_key_wins_over_the_legacy_one():
    migrated, changed = config_mod._migrate(
        {"open_hotkey": ["ctrl", "alt", "o"], "command_hotkey": ["ctrl", "win", "k"]}
    )
    assert migrated["command_hotkey"] == ["ctrl", "win", "k"]
    assert "open_hotkey" in migrated


def test_migration_reports_no_change_when_there_is_nothing_to_do():
    assert config_mod._migrate({"command_hotkey": ["ctrl", "alt", "o"]}) == (
        {"command_hotkey": ["ctrl", "alt", "o"]},
        False,
    )


def test_a_saved_legacy_config_is_rewritten_with_the_new_names(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = config_mod.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"open_hotkey": ["ctrl", "win", "j"], "open_with_voice": False}),
        encoding="utf-8",
    )

    cfg = config_mod.load_config()
    assert cfg["command_hotkey"] == ["ctrl", "win", "j"]
    assert cfg["command_with_voice"] is False

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "open_hotkey" not in on_disk
    assert on_disk["command_hotkey"] == ["ctrl", "win", "j"]


def test_config_roundtrips_through_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    cfg = config_mod.load_config()
    cfg["language"] = "pt"
    cfg["hotkey"] = ["ctrl", "shift", "m"]
    assert config_mod.save_config(cfg)

    loaded = config_mod.load_config()
    assert loaded["language"] == "pt"
    assert loaded["hotkey"] == ["ctrl", "shift", "m"]


def test_config_survives_corrupt_file(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = config_mod.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert config_mod.load_config()["hotkey"] == list(config_mod.DEFAULTS["hotkey"])


# ------------------------------------------------------------ atomic writes
def test_save_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config_mod.save_config(config_mod.Config(config_mod.DEFAULTS))
    assert config_mod.config_path().exists()
    assert list(tmp_path.rglob("*.tmp")) == []


def test_a_failed_write_leaves_the_previous_config_intact(tmp_path, monkeypatch):
    """config.json is rewritten on every tray toggle.

    A partial write used to truncate it, which reset every setting at once.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    good = config_mod.Config(config_mod.DEFAULTS)
    good["language"] = "pt"
    assert config_mod.save_config(good)

    real_replace = config_mod.os.replace

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(config_mod.os, "replace", boom)
    broken = config_mod.Config(config_mod.DEFAULTS)
    broken["language"] = "es"
    assert config_mod.save_config(broken) is False

    monkeypatch.setattr(config_mod.os, "replace", real_replace)
    assert config_mod.load_config()["language"] == "pt"  # untouched
    assert list(tmp_path.rglob("*.tmp")) == []  # no debris


def test_the_api_key_is_written_atomically_too(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert config_mod.write_api_key("gsk_abc")
    assert config_mod.read_api_key() == "gsk_abc"
    assert list(tmp_path.rglob("*.tmp")) == []


def test_config_survives_a_byte_order_mark(tmp_path, monkeypatch):
    """Notepad writes a BOM, and that used to read as a corrupt file."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    path = config_mod.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"language": "pt", "command_hotkey": ["ctrl", "win", "j"]}),
        encoding="utf-8-sig",
    )
    cfg = config_mod.load_config()
    assert cfg["language"] == "pt"
    assert cfg["command_hotkey"] == ["ctrl", "win", "j"]


def test_api_key_prefers_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_env")
    assert config_mod.read_api_key() == "gsk_from_env"

    monkeypatch.delenv("GROQ_API_KEY")
    config_mod.write_api_key("gsk_from_file")
    assert config_mod.read_api_key() == "gsk_from_file"


# ------------------------------------------------------------------------ cli
def test_cli_accepts_a_new_command_hotkey(tmp_path, monkeypatch, capsys):
    from stt.__main__ import main

    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert main(["--command-hotkey", "ctrl+win+j", "--check"]) == 0
    assert config_mod.load_config()["command_hotkey"] == ["ctrl", "win", "j"]


def test_cli_rejects_an_invalid_command_hotkey(tmp_path, monkeypatch, capsys):
    from stt.__main__ import main

    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert main(["--command-hotkey", "ctrl+alt", "--check"]) == 2
    assert "--command-hotkey" in capsys.readouterr().err


def test_cli_rejects_two_identical_hotkeys(tmp_path, monkeypatch, capsys):
    from stt.__main__ import main

    monkeypatch.setenv("APPDATA", str(tmp_path))
    code = main(["--hotkey", "ctrl+alt+q", "--command-hotkey", "ctrl+alt+q", "--check"])
    assert code == 2
    assert "must differ" in capsys.readouterr().err


# -------------------------------------------------------------- transcribe
def test_pcm_to_wav_is_valid_16k_mono():
    pcm = np.zeros(16000, dtype=np.int16)
    data = transcribe.pcm_to_wav(pcm, 16000)
    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert len(data) > 16000 * 2  # header + payload


def test_transcribe_requires_api_key():
    with pytest.raises(transcribe.TranscriptionError, match="API key"):
        transcribe.transcribe(b"RIFF...", api_key="")


def test_transcribe_rejects_empty_audio():
    with pytest.raises(transcribe.TranscriptionError, match="No audio"):
        transcribe.transcribe(b"", api_key="gsk_x")


def test_transcribe_sends_zero_temperature_and_language(monkeypatch):
    captured = {}

    class Resp:
        status_code = 200
        ok = True

        def json(self):
            return {"text": "Hello world."}

    def fake_post(url, headers=None, files=None, data=None, timeout=None):
        captured.update(url=url, headers=headers, data=data, files=files)
        return Resp()

    monkeypatch.setattr(transcribe.requests, "post", fake_post)
    out = transcribe.transcribe(
        b"RIFF...", api_key="gsk_x", language="pt", prompt="Casual tone"
    )

    assert out == "Hello world."
    assert captured["data"]["temperature"] == "0"
    assert captured["data"]["language"] == "pt"
    assert captured["data"]["model"] == "whisper-large-v3"
    assert captured["data"]["prompt"] == "Casual tone"
    assert captured["headers"]["Authorization"] == "Bearer gsk_x"


def test_transcribe_omits_language_when_auto(monkeypatch):
    captured = {}

    class Resp:
        status_code = 200
        ok = True

        def json(self):
            return {"text": "ok"}

    monkeypatch.setattr(
        transcribe.requests,
        "post",
        lambda url, headers=None, files=None, data=None, timeout=None: (
            captured.update(data=data),
            Resp(),
        )[1],
    )
    transcribe.transcribe(b"RIFF...", api_key="gsk_x", language="auto")
    assert "language" not in captured["data"]


@pytest.mark.parametrize("code,expected", [(401, "API key"), (429, "rate limit")])
def test_transcribe_maps_http_errors(monkeypatch, code, expected):
    class Resp:
        status_code = code
        ok = False
        text = "boom"

    monkeypatch.setattr(
        transcribe.requests,
        "post",
        lambda *a, **k: Resp(),
    )
    with pytest.raises(transcribe.TranscriptionError, match=expected):
        transcribe.transcribe(b"RIFF...", api_key="gsk_x")


def test_transcribe_filters_silence_hallucinations(monkeypatch):
    class Resp:
        status_code = 200
        ok = True

        def __init__(self, text):
            self._text = text

        def json(self):
            return {"text": self._text}

    for junk in (
        " [BLANK_AUDIO] ",
        "(silence)",
        "[MUSIC]",
        "Subtitles by the Amara.org community",
        "www.amara.org",
    ):
        monkeypatch.setattr(
            transcribe.requests, "post", lambda *a, _j=junk, **k: Resp(_j)
        )
        with pytest.raises(transcribe.TranscriptionError, match="No speech"):
            transcribe.transcribe(b"RIFF...", api_key="gsk_x")


@pytest.mark.parametrize(
    "real",
    ["Thank you.", "you", "Bye.", "You", "Thanks for watching!", "Ok."],
)
def test_transcribe_keeps_a_short_utterance_that_was_actually_spoken(real):
    """A one-word dictation is not a hallucination.

    These used to be dropped as junk, so dictating "thank you." came back as
    "No speech detected." after a paid round trip. Short and common is exactly
    what a person often says.
    """
    assert transcribe._strip_hallucinations(real) == real


def test_strip_hallucinations_keeps_speech_that_mentions_a_watermark():
    assert transcribe._strip_hallucinations("go to amara.org") == "go to amara.org"


def test_strip_hallucinations_still_drops_empty():
    assert transcribe._strip_hallucinations("   ") == ""


# ------------------------------------------------------------------- timeout
def test_timeout_scales_with_the_audio():
    # 16 kHz mono int16 = 32000 B/s; the WAV header adds a constant 44 bytes.
    short = transcribe.pcm_to_wav(np.zeros(2 * 16000, dtype=np.int16))
    assert transcribe.timeout_for(short) == transcribe.MIN_TIMEOUT

    two_minutes = transcribe.pcm_to_wav(np.zeros(120 * 16000, dtype=np.int16))
    assert transcribe.timeout_for(two_minutes) == int(120 * 1.5 + 15)


def test_timeout_is_clamped_at_both_ends():
    assert transcribe.timeout_for(b"RIFF" * 4) == transcribe.MIN_TIMEOUT
    huge = transcribe.pcm_to_wav(np.zeros(600 * 16000, dtype=np.int16))
    assert transcribe.timeout_for(huge) == transcribe.MAX_TIMEOUT


def test_transcribe_scales_the_timeout_it_sends(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200
        ok = True

        def json(self):
            return {"text": "hello there"}

    def post(*a, **k):
        seen.update(k)
        return Resp()

    monkeypatch.setattr(transcribe.requests, "post", post)
    transcribe.transcribe(b"RIFF..." * 100_000, api_key="gsk_x")
    assert seen["timeout"] > transcribe.MIN_TIMEOUT


def test_transcribe_timeout_error_reports_the_duration(monkeypatch):
    def post(*a, **k):
        raise transcribe.requests.Timeout()

    monkeypatch.setattr(transcribe.requests, "post", post)
    with pytest.raises(transcribe.TranscriptionError, match=r"timed out after \d+s"):
        transcribe.transcribe(b"RIFF...", api_key="gsk_x")
