"""Global push-to-talk hotkey.

pynput's low-level hook gives press *and* release events, which is what a
hold-to-talk needs (a plain `add_hotkey` only fires on the trigger press).
Key-repeat while held is filtered by an internal flag so one physical press
produces exactly one start event.

The hook thread is not the Qt thread, so callbacks are forwarded through
`on_start` / `on_stop` supplied by the caller, which marshals them.
"""

from __future__ import annotations

import threading

from pynput import keyboard

from . import keys as keyutil


class PushToTalkListener:
    """Watch for a modifier+key combo held down."""

    def __init__(self, combo, on_start, on_stop, on_error=None):
        self.combo = keyutil.parse_combo("+".join(combo))
        self.modifiers = keyutil.modifiers_of(self.combo)
        self.trigger = keyutil.trigger_of(self.combo)
        self._on_start = on_start
        self._on_stop = on_stop
        self._on_error = on_error

        self._held: set[str] = set()
        self._active = False
        self._lock = threading.Lock()
        self._listener: keyboard.Listener | None = None

    @property
    def label(self) -> str:
        return keyutil.combo_label(self.combo)

    def start(self) -> None:
        if self._listener is not None:
            return
        self._listener = keyboard.Listener(
            on_press=self._press, on_release=self._release, suppress=False
        )
        self._listener.daemon = True
        try:
            self._listener.start()
        except Exception as exc:  # pragma: no cover - environment dependent
            self._listener = None
            if self._on_error:
                self._on_error(f"Keyboard hook failed: {exc}")

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        with self._lock:
            self._held.clear()
            was_active, self._active = self._active, False
        if was_active:
            self._on_stop()

    @property
    def is_held(self) -> bool:
        return self._active

    # ------------------------------------------------------------- internals
    def _press(self, key) -> None:
        names = self._names(key)
        if not names:
            return
        with self._lock:
            for name in names:
                self._held.add(name)
            if self._active:
                return
            if self.trigger not in names:
                return
            if not self.modifiers.issubset(self._held):
                return
            self._active = True
        self._fire(self._on_start)

    def _release(self, key) -> None:
        names = self._names(key)
        if not names:
            return
        with self._lock:
            for name in names:
                self._held.discard(name)
            # Releasing a modifier also ends the hold: the user let go of the
            # combo, so the recording should stop rather than wait for a key-up
            # that may never come.
            if not self._active:
                return
            if not any(n == self.trigger or n in self.modifiers for n in names):
                return
            self._active = False
        self._fire(self._on_stop)

    def _fire(self, callback) -> None:
        try:
            callback()
        except Exception as exc:  # pragma: no cover - defensive
            if self._on_error:
                self._on_error(str(exc))

    def _names(self, key) -> tuple[str, ...]:
        """Every token this key can stand for, most specific first.

        A single keystroke has up to three identities: the character it
        produced, its virtual key code, and its name. Keeping all of them lets
        the trigger match whether Windows handed us a usable character or a
        mangled one.
        """
        found: list[str] = []

        char = getattr(key, "char", None)
        if char and len(char) == 1 and char.isprintable() and char != "\ufffd":
            token = "space" if char == " " else char.lower()
            found.append(token)

        vk_token = keyutil.token_for_vk(getattr(key, "vk", None))
        if vk_token:
            found.append(vk_token)

        raw = getattr(key, "name", None)
        if raw:
            name = keyutil.normalize(raw)
            if name:
                found.append(name)

        # Keep the first spelling of each identity so 'M' and 'm' collapse.
        return tuple(dict.fromkeys(found))
