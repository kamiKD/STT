"""Application wiring: tray, overlay, hotkey, recorder, worker thread.

Threading model
  * Qt main thread - overlay painting and tray menu.
  * pynput hook thread - hotkey events, marshalled via a queued signal.
  * QThread worker - does network transcription so the UI never blocks.

Two independent hotkeys feed the same capture pipeline and differ only in what
happens to the transcript: the dictation combo types into the focused window,
the command combo acts on what the user said.
"""

from __future__ import annotations

import os
import sys
import threading

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QSystemTrayIcon

from . import config as config_mod
from . import keys as keyutil
from . import transcribe, typing, voice_command
from .audio import Recorder
from .hotkey import PushToTalkListener
from .hotkey_dialog import HotkeyDialog
from .overlay import RecordingOverlay

IDLE_HIDE_MS = 3500

MODE_DICTATE = "dictate"
MODE_COMMAND = "command"

TRAY_BODY = {
    MODE_DICTATE: ("#ff5c5c", "#3c3f4b"),
    MODE_COMMAND: ("#7aa2ff", "#3c4a6b"),
}


def _tray_icon(active: bool, mode: str = MODE_DICTATE) -> QIcon:
    hot, idle = TRAY_BODY.get(mode, TRAY_BODY[MODE_DICTATE])
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    body = QColor(hot) if active else QColor(idle)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(body)
    # Simple mic glyph: capsule + stem + base.
    p.drawRoundedRect(26, 10, 12, 26, 6, 6)
    p.setBrush(Qt.GlobalColor.transparent)
    p.setPen(QPen(body, 4))
    p.drawArc(16, 22, 32, 30, 0, 180 * 16)
    p.drawLine(32, 52, 32, 60)
    p.drawLine(22, 60, 42, 60)
    p.end()
    return QIcon(pm)


class _ResultBridge(QObject):
    """Carries the worker's result back to the GUI thread.

    A plain QObject signal is emitted from a plain `threading.Thread`. Because
    the bridge lives in the GUI thread, Qt queues the delivery, so the handler
    runs where it is safe to touch widgets. This is used instead of QThread +
    moveToThread, which crashes when the moved object is dropped from the
    thread that created it.
    """

    finished = Signal(str, str, str)  # (text, error, mode)


class _HotkeyBridge(QObject):
    """Moves hook-thread callbacks onto the Qt thread via a queued signal."""

    pressed = Signal(str)  # mode
    released = Signal(str)  # mode
    error = Signal(str)


class App:
    def __init__(self, cfg: config_mod.Config):
        self.cfg = cfg
        self.api_key = config_mod.read_api_key()
        self.recorder = Recorder(
            sample_rate=cfg["sample_rate"], max_seconds=cfg["max_seconds"]
        )
        self.overlay = RecordingOverlay()
        self.bridge = _HotkeyBridge()
        self.result = _ResultBridge()
        self._thread: threading.Thread | None = None
        self._busy = False
        self._mode = MODE_DICTATE
        self._capturing = False
        self._last_text = ""

        self.bridge.pressed.connect(self._on_press, Qt.ConnectionType.QueuedConnection)
        self.bridge.released.connect(self._on_release, Qt.ConnectionType.QueuedConnection)
        self.bridge.error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        self.result.finished.connect(self._on_transcribed, Qt.ConnectionType.QueuedConnection)

        # Safety net for a lost key-up: force the recording to end. Parented to
        # the bridge because App is a plain object, not a QObject.
        self._max_timer = QTimer(self.bridge)
        self._max_timer.setSingleShot(True)
        self._max_timer.timeout.connect(self._force_release)

        self.listener = self._make_listener(MODE_DICTATE, cfg["hotkey"])
        self.command_listener = self._make_listener(MODE_COMMAND, cfg["command_hotkey"])

        self._build_tray()

    # --------------------------------------------------------------- listeners
    def _make_listener(self, mode: str, combo) -> PushToTalkListener:
        return PushToTalkListener(
            combo,
            on_start=lambda m=mode: self.bridge.pressed.emit(m),
            on_stop=lambda m=mode: self.bridge.released.emit(m),
            on_error=self.bridge.error.emit,
        )

    def _bind_hotkey(self, which: str, combo) -> None:
        """Swap in a new combo for `which` and restart that hook."""
        # Drop any capture first: stopping a listener mid-hold fires its
        # synthetic on_stop, which would otherwise transcribe a stray buffer.
        if self.recorder.is_recording:
            self.recorder.abort()
            self._max_timer.stop()
            self.tray.setIcon(_tray_icon(False))
        if which == "hotkey":
            self.listener.stop()
            self.listener = self._make_listener(MODE_DICTATE, combo)
            self.listener.start()
            return
        if self.command_listener is not None:
            self.command_listener.stop()
            self.command_listener = None
        if self.cfg["command_with_voice"]:
            self.command_listener = self._make_listener(MODE_COMMAND, combo)
            self.command_listener.start()

    # ------------------------------------------------------------------- tray
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(_tray_icon(False), None)
        self.tray.setToolTip("Speech To Text")
        self.tray.activated.connect(self._on_tray_activated)
        self._rebuild_menu()
        self.tray.show()

    def _rebuild_menu(self) -> None:
        menu = QMenu()
        label = keyutil.combo_label(self.cfg["hotkey"])
        info = menu.addAction(f"Hold {label} to dictate")
        info.setEnabled(False)
        menu.addSeparator()

        toggle = QAction("Enabled", menu)
        toggle.setCheckable(True)
        toggle.setChecked(self.cfg["auto_type"])
        toggle.toggled.connect(self._on_toggle_enabled)
        menu.addAction(toggle)

        backup = QAction("Copy to clipboard as backup", menu)
        backup.setCheckable(True)
        backup.setChecked(self.cfg["clipboard_backup"])
        backup.toggled.connect(self._on_toggle_backup)
        menu.addAction(backup)

        menu.addSeparator()

        voice = QAction("Command with Voice", menu)
        voice.setCheckable(True)
        voice.setChecked(bool(self.cfg["command_with_voice"]))
        voice.toggled.connect(self._on_toggle_command)
        menu.addAction(voice)

        command_label = keyutil.combo_label(self.cfg["command_hotkey"])
        voice_hint = menu.addAction(
            f'Hold {command_label} - "open X", "close X", "lock the screen"'
        )
        voice_hint.setEnabled(False)

        system = QAction("Allow system commands", menu)
        system.setCheckable(True)
        system.setChecked(bool(self.cfg["command_system_enabled"]))
        system.toggled.connect(self._on_toggle_system)
        menu.addAction(system)

        set_command = QAction("Set command hotkey...", menu)
        set_command.triggered.connect(lambda: self._on_edit_hotkey("command_hotkey"))
        menu.addAction(set_command)

        set_dictate = QAction("Set dictation hotkey...", menu)
        set_dictate.triggered.connect(lambda: self._on_edit_hotkey("hotkey"))
        menu.addAction(set_dictate)

        menu.addSeparator()

        model_menu = menu.addMenu(f"Model: {self.cfg['model']}")
        for name in config_mod.MODELS:
            act = model_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self.cfg["model"])
            act.triggered.connect(lambda _c=False, n=name: self._on_pick_model(n))

        lang_menu = menu.addMenu(
            f"Language: {config_mod.LANGUAGES.get(self.cfg['language'], self.cfg['language'])}"
        )
        for code, name in config_mod.LANGUAGES.items():
            act = lang_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(code == self.cfg["language"])
            act.triggered.connect(lambda _c=False, c=code: self._on_pick_language(c))

        key_state = config_mod.mask_key(self.api_key)
        key_act = menu.addAction(f"Groq API key: {key_state}")
        key_act.triggered.connect(self._on_set_key)

        menu.addSeparator()
        autostart = QAction("Start with Windows", menu)
        autostart.setCheckable(True)
        autostart.setChecked(self._is_autostart_enabled())
        autostart.toggled.connect(self._on_toggle_autostart)
        menu.addAction(autostart)

        copy_last = QAction("Copy last transcript", menu)
        copy_last.triggered.connect(self._on_copy_last)
        menu.addAction(copy_last)

        menu.addSeparator()
        quit_act = QAction("Quit", menu)
        quit_act.triggered.connect(self.shutdown)
        menu.addAction(quit_act)

        self.tray.setContextMenu(menu)
        self._menu = menu

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.overlay.set_state("done", self._last_text or "Ready.")

    # ---------------------------------------------------------------- hotkey
    def start(self) -> None:
        self.listener.start()
        if self.cfg["command_with_voice"] and self.command_listener is not None:
            self.command_listener.start()
        if not self.api_key:
            self.overlay.set_state("error", "No Groq API key. Right-click the tray icon to add one.")
            self.overlay.schedule_hide(6000)
        self._warm_indexes()
        self._announce_ready()

    @staticmethod
    def _warm_indexes() -> None:
        """Walk the Start Menu and the folder tree before anyone asks.

        Both walks cost a fraction of a second, which is long enough to freeze
        the overlay if the first spoken command pays for it on the Qt thread.
        """
        def warm() -> None:
            try:
                voice_command.cached_index()
                voice_command.cached_folders()
            except Exception:  # pragma: no cover - filesystem dependent
                pass

        threading.Thread(target=warm, name="stt-index", daemon=True).start()

    def _announce_ready(self) -> None:
        if self.api_key:
            self.overlay.set_state("done", f"Hold {self.listener.label} to dictate.")
            self.overlay.schedule_hide(2500)

    def _on_press(self, mode: str = MODE_DICTATE) -> None:
        if self._capturing or self._busy or self.recorder.is_recording:
            return
        if mode == MODE_COMMAND:
            if not self.cfg["command_with_voice"]:
                return
        elif not self.cfg["auto_type"]:
            return
        if not self.api_key:
            self.overlay.set_state("error", "No Groq API key set.")
            self.overlay.schedule_hide(4000)
            return
        try:
            self.recorder.start()
        except Exception as exc:
            self.overlay.set_state("error", f"Microphone unavailable: {exc}")
            self.overlay.schedule_hide(5000)
            return
        self._mode = mode
        self.tray.setIcon(_tray_icon(True, mode))
        if mode == MODE_COMMAND:
            hint = 'Say "open Chrome", "close Notepad" or "lock the screen".'
        else:
            hint = f"Speak now - release {self.listener.label} to insert."
        self.overlay.set_state("recording", hint)
        # Hard stop in case the release event is lost (focus change, session
        # lock, hook hiccup). A stuck overlay would be far more annoying than
        # truncating a long dictation.
        self._max_timer.start(self.cfg["max_seconds"] * 1000 + 1500)

    def _on_release(self, mode: str | None = None) -> None:
        if not self.recorder.is_recording:
            return
        mode = mode or self._mode
        self._max_timer.stop()
        pcm = self.recorder.stop()
        self.tray.setIcon(_tray_icon(False))
        if pcm.size == 0:
            self.overlay.set_state("error", "No audio captured.")
            self.overlay.schedule_hide()
            return

        rms = self.recorder._rms(pcm)
        if rms < self.cfg["min_rms"]:
            self.overlay.set_state("error", "Nothing audible - check the mic.")
            self.overlay.schedule_hide()
            return

        self._busy = True
        self.overlay.set_state("transcribing", f"{pcm.size / self.cfg['sample_rate']:.1f}s of audio")
        wav_bytes = transcribe.pcm_to_wav(pcm, self.cfg["sample_rate"])

        cfg_snapshot = dict(self.cfg)
        api_key = self.api_key

        def work() -> None:
            try:
                text = transcribe.transcribe(
                    wav_bytes,
                    api_key=api_key,
                    model=cfg_snapshot["model"],
                    language=cfg_snapshot["language"],
                    prompt=cfg_snapshot.get("prompt", ""),
                )
            except transcribe.TranscriptionError as exc:
                self.result.finished.emit("", str(exc), mode)
                return
            except Exception as exc:  # pragma: no cover - defensive
                self.result.finished.emit("", f"Unexpected error: {exc}", mode)
                return
            self.result.finished.emit(text, "", mode)

        self._thread = threading.Thread(target=work, name="stt-transcribe", daemon=True)
        self._thread.start()

    def _force_release(self) -> None:
        if self.recorder.is_recording:
            self.overlay.set_state("recording", "Reached the length limit - transcribing.")
            self._on_release()

    def _on_transcribed(self, text: str, error: str, mode: str = MODE_DICTATE) -> None:
        self._busy = False
        self._thread = None

        if error:
            self.overlay.set_state("error", error)
            self.overlay.schedule_hide()
            return

        self._last_text = text

        if mode == MODE_COMMAND:
            # No transcript line: what the overlay shows is the outcome.
            self._handle_command(text)
            return

        self.overlay.set_transcript(text)
        # A trailing space keeps consecutive dictations from running together.
        payload = text + " " if self.cfg["trailing_space"] else text

        method = typing.deliver(
            payload,
            delay_ms=self.cfg["type_delay_ms"],
            clipboard_backup=self.cfg["clipboard_backup"],
        )
        self.overlay.set_state("done", f"Inserted ({method}): {text}")
        self.overlay.schedule_hide()

    def _handle_command(self, text: str) -> None:
        """Act on a spoken command, never typing into the focused window.

        Nothing is inserted on this path: the whole point is that the window you
        were reading keeps its content.
        """
        result = voice_command.execute_text(
            text,
            aliases=self.cfg["command_aliases"],
            system_enabled=self.cfg["command_system_enabled"],
        )
        if result.ok:
            self.overlay.set_state("done", f"{result.name}.")
            self.overlay.schedule_hide(3000)
            return

        message = result.message
        if result.suggestions:
            message += " Did you mean " + " or ".join(result.suggestions) + "?"
        self.overlay.set_state("error", message)
        self.overlay.schedule_hide(5000)

    def _on_error(self, message: str) -> None:
        self.overlay.set_state("error", message)
        self.overlay.schedule_hide()

    # ------------------------------------------------------------- menu slots
    def _on_toggle_enabled(self, value: bool) -> None:
        self.cfg["auto_type"] = value
        self.cfg.save()

    def _on_toggle_backup(self, value: bool) -> None:
        self.cfg["clipboard_backup"] = value
        self.cfg.save()

    def _on_toggle_command(self, value: bool) -> None:
        self.cfg["command_with_voice"] = value
        self.cfg.save()
        if self.command_listener is not None:
            self.command_listener.stop()
            self.command_listener = None
        if value:
            self.command_listener = self._make_listener(
                MODE_COMMAND, self.cfg["command_hotkey"]
            )
            self.command_listener.start()
            self.overlay.set_state(
                "done",
                f'Hold {keyutil.combo_label(self.cfg["command_hotkey"])} and say a command.',
            )
            self.overlay.schedule_hide(4000)
        self._rebuild_menu()

    def _on_toggle_system(self, value: bool) -> None:
        self.cfg["command_system_enabled"] = value
        self.cfg.save()

    def _on_edit_hotkey(self, which: str) -> None:
        """Ask for a combo by keystroke, then rebind and save it."""
        if which == "hotkey":
            title = "Dictation hotkey"
            hint = "Press the combo you want to hold while dictating."
        else:
            title = "Command hotkey"
            hint = 'Press the combo you want to hold while saying "open <app>".'

        # The dialog grabs the keyboard, but our own low-level hook still sees
        # the presses; ignore them so binding a combo never starts a recording.
        self._capturing = True
        try:
            dialog = HotkeyDialog(title, hint, self.cfg[which], self.overlay)
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            combo = dialog.combo()
        finally:
            self._capturing = False

        if not accepted or not combo:
            return

        other = "command_hotkey" if which == "hotkey" else "hotkey"
        if combo == list(self.cfg[other]):
            taken = "command" if which == "hotkey" else "dictation"
            self.overlay.set_state(
                "error",
                f"{keyutil.combo_label(combo)} is already the {taken} hotkey.",
            )
            self.overlay.schedule_hide(4000)
            return

        self._bind_hotkey(which, combo)
        self.cfg[which] = combo
        self.cfg.save()
        self._rebuild_menu()
        self.overlay.set_state("done", f"{title}: {keyutil.combo_label(combo)}")
        self.overlay.schedule_hide(2500)

    def _on_pick_model(self, name: str) -> None:
        self.cfg["model"] = name
        self.cfg.save()
        self._rebuild_menu()

    def _on_pick_language(self, code: str) -> None:
        self.cfg["language"] = code
        self.cfg.save()
        self._rebuild_menu()

    def _on_copy_last(self) -> None:
        if self._last_text:
            typing.copy_to_clipboard(self._last_text)
            self.overlay.set_state("done", "Last transcript copied.")
            self.overlay.schedule_hide(2000)

    def _on_set_key(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QLineEdit

        text, ok = QInputDialog.getText(
            None,
            "Groq API key",
            "Paste your key from console.groq.com/keys.\nIt is stored in "
            f"{config_mod.key_path()}",
            QLineEdit.EchoMode.Password,
            self.api_key,
        )
        if ok and text.strip():
            config_mod.write_api_key(text.strip())
            self.api_key = text.strip()
            self._rebuild_menu()
            self._announce_ready()

    # -------------------------------------------------------------- autostart
    def _autostart_cmd(self) -> str:
        exe = sys.executable
        if exe.lower().endswith("python.exe"):
            pythonw = exe[: -len("python.exe")] + "pythonw.exe"
            if os.path.exists(pythonw):
                exe = pythonw  # no console window at login
        script = config_mod.app_dir() / "start.vbs"
        if script.exists():
            return f'wscript.exe "{script}"'
        # -m stt needs the package dir on the path when launched from elsewhere.
        project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return f'"{exe}" -m stt' if project == os.getcwd() else f'cd /d "{project}" && "{exe}" -m stt'

    def _is_autostart_enabled(self) -> bool:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "SpeechToText")
                return bool(value)
        except OSError:
            return False

    def _on_toggle_autostart(self, value: bool) -> None:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                if value:
                    winreg.SetValueEx(key, "SpeechToText", 0, winreg.REG_SZ, self._autostart_cmd())
                else:
                    try:
                        winreg.DeleteValue(key, "SpeechToText")
                    except FileNotFoundError:
                        pass
        except OSError as exc:
            self.overlay.set_state("error", f"Autostart change failed: {exc}")
            self.overlay.schedule_hide(4000)
        self._rebuild_menu()

    # --------------------------------------------------------------- shutdown
    def shutdown(self) -> None:
        self._max_timer.stop()
        if self.recorder.is_recording:
            self.recorder.abort()
        self.listener.stop()
        if self.command_listener is not None:
            self.command_listener.stop()
            self.command_listener = None
        self.tray.hide()
        QApplication.quit()


def run(cfg: config_mod.Config | None = None) -> int:
    cfg = cfg or config_mod.load_config()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Speech To Text")

    controller = App(cfg)
    # Keep a reference alive: without it the controller is collected and the
    # hotkey hook dies with it.
    app._stt = controller

    # Feed the overlay's level meter while recording. Parented to the bridge so
    # it is torn down with the app.
    meter = QTimer(controller.bridge)
    meter.setInterval(50)
    meter.timeout.connect(
        lambda: controller.overlay.set_level(controller.recorder.level())
    )
    meter.start()
    app._stt_meter = meter

    app.aboutToQuit.connect(controller.shutdown)
    controller.start()
    return app.exec()
