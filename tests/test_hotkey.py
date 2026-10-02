"""Tests for the hold-to-talk hotkey state machine.

The pynput hook itself is Windows-only, so the press/release handlers are driven
directly with pynput key objects.
"""

from __future__ import annotations

from pynput.keyboard import Key, KeyCode

from stt.hotkey import PushToTalkListener


def build(combo=("ctrl", "alt", "space")):
    events = []
    listener = PushToTalkListener(
        list(combo),
        on_start=lambda: events.append("start"),
        on_stop=lambda: events.append("stop"),
    )
    return listener, events


def test_full_hold_emits_start_then_stop():
    listener, events = build()
    listener._press(Key.ctrl_l)
    listener._press(Key.alt_l)
    listener._press(KeyCode(char=" "))
    listener._release(KeyCode(char=" "))
    assert events == ["start", "stop"]


def test_key_repeat_does_not_retrigger():
    listener, events = build()
    listener._press(Key.ctrl_l)
    listener._press(Key.alt_l)
    for _ in range(5):
        listener._press(KeyCode(char=" "))
    assert events == ["start"]
    listener._release(KeyCode(char=" "))
    assert events == ["start", "stop"]


def test_bare_space_is_ignored():
    listener, events = build()
    listener._press(KeyCode(char=" "))
    listener._release(KeyCode(char=" "))
    assert events == []


def test_missing_one_modifier_is_ignored():
    listener, events = build()
    listener._press(Key.ctrl_l)
    listener._press(KeyCode(char=" "))
    listener._release(KeyCode(char=" "))
    assert events == []


def test_trigger_before_modifier_does_not_trigger():
    listener, events = build()
    listener._press(KeyCode(char=" "))
    listener._press(Key.ctrl_l)
    listener._press(Key.alt_l)
    assert events == []


def test_releasing_a_modifier_ends_the_hold():
    """Letting go of Ctrl first must stop recording, not wait forever."""
    listener, events = build()
    listener._press(Key.ctrl_l)
    listener._press(Key.alt_l)
    listener._press(KeyCode(char=" "))
    listener._release(Key.alt_l)
    assert events == ["start", "stop"]


def test_releasing_an_unrelated_key_keeps_recording():
    listener, events = build()
    listener._press(Key.ctrl_l)
    listener._press(Key.alt_l)
    listener._press(KeyCode(char=" "))
    listener._press(Key.shift_l)
    listener._release(Key.shift_l)
    assert events == ["start"]
    listener._release(KeyCode(char=" "))
    assert events == ["start", "stop"]


def test_repeat_hold_cycles_cleanly():
    listener, events = build()
    for _ in range(3):
        listener._press(Key.ctrl_l)
        listener._press(Key.alt_l)
        listener._press(KeyCode(char=" "))
        listener._release(KeyCode(char=" "))
        listener._release(Key.alt_l)
        listener._release(Key.ctrl_l)
    assert events == ["start", "stop"] * 3


def test_right_modifier_variants_count():
    listener, events = build()
    listener._press(Key.ctrl_r)
    listener._press(Key.alt_r)
    listener._press(Key.space)
    listener._release(Key.space)
    assert events == ["start", "stop"]


def test_non_space_trigger_supported():
    listener, events = build(combo=("ctrl", "shift", "m"))
    listener._press(Key.ctrl_l)
    listener._press(Key.shift_l)
    listener._press(KeyCode(char="m", vk=0x4D))
    listener._release(KeyCode(char="m", vk=0x4D))
    assert events == ["start", "stop"]


def test_label_is_display_ready():
    listener, _ = build()
    assert listener.label == "Ctrl+Alt+Space"


def test_stop_while_held_fires_synthetic_release():
    listener, events = build()
    listener._press(Key.ctrl_l)
    listener._press(Key.alt_l)
    listener._press(KeyCode(char=" "))
    listener.stop()
    assert events == ["start", "stop"]
    assert listener.is_held is False


def test_stop_when_idle_is_quiet():
    listener, events = build()
    listener.stop()
    assert events == []


def test_unknown_key_is_ignored_gracefully():
    listener, events = build()
    listener._press(KeyCode(char="\x00"))
    listener._release(KeyCode(char="\x00"))
    assert events == []


# --------------------------------------------------- alt-modified letter keys
def test_letter_trigger_matches_on_the_virtual_key_alone():
    """Windows reports Ctrl+Alt+O as a mangled char and no name; only the vk
    is left, so a char-only matcher would never fire on the default combo."""
    listener, events = build(combo=("ctrl", "alt", "o"))
    mangled = KeyCode(char="\ufffd", vk=0x4F)
    listener._press(Key.ctrl_l)
    listener._press(Key.alt_l)
    listener._press(mangled)
    listener._release(mangled)
    assert events == ["start", "stop"]


def test_mangled_char_alone_is_not_enough():
    """The same object must not fire a combo bound to some other letter."""
    listener, events = build(combo=("ctrl", "alt", "k"))
    listener._press(Key.ctrl_l)
    listener._press(Key.alt_l)
    listener._press(KeyCode(char="\ufffd", vk=0x4F))
    listener._release(KeyCode(char="\ufffd", vk=0x4F))
    assert events == []


def test_uppercase_char_matches_a_lowercase_trigger():
    listener, events = build(combo=("ctrl", "shift", "m"))
    listener._press(Key.ctrl_l)
    listener._press(Key.shift_l)
    listener._press(KeyCode(char="M", vk=0x4D))
    listener._release(KeyCode(char="M", vk=0x4D))
    assert events == ["start", "stop"]


def test_two_combos_coexist():
    """The app runs one listener per hotkey; a press must reach only its own."""
    events = []
    dictate = PushToTalkListener(
        ["ctrl", "alt", "space"],
        on_start=lambda: events.append("dictate"),
        on_stop=lambda: events.append("dictate-stop"),
    )
    voice = PushToTalkListener(
        ["ctrl", "alt", "o"],
        on_start=lambda: events.append("voice"),
        on_stop=lambda: events.append("voice-stop"),
    )
    for listener in (dictate, voice):
        listener._press(Key.ctrl_l)
        listener._press(Key.alt_l)

    dictate._press(KeyCode(char=" "))
    voice._press(KeyCode(char="\ufffd", vk=0x4F))
    assert events == ["dictate", "voice"]

    voice._release(KeyCode(char="\ufffd", vk=0x4F))
    dictate._release(KeyCode(char=" "))
    assert events == ["dictate", "voice", "voice-stop", "dictate-stop"]
