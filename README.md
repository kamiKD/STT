# Speech To Text

Hold `Ctrl+Alt+Space`, speak, release. The transcript is typed straight into
whatever window you were already using — Word, Chrome, VS Code, a game chat
box. No window switching, no clipboard dance.

Hold `Ctrl+Alt+O`, say "open Chrome", "close Notepad" or "lock the screen",
release. The machine does it, and nothing is typed anywhere.

Runs on Windows, in the tray, out of the way.

MIT licensed. Windows only: the hotkey is a low-level keyboard hook and audio
goes through `sounddevice`, so there is no macOS or Linux path today.

## Install

```
pip install -r requirements.txt
```

Get a free API key from <https://console.groq.com/keys> and either set it as an
environment variable:

```
setx GROQ_API_KEY gsk_...
```

or right-click the tray icon → `Groq API key` and paste it. The app stores it
in `%APPDATA%\Speech-To-Text\groq_key.txt` (plain text, only that file is worth
protecting if the machine is shared).

## Run

```
python -m stt
```

Check the setup without starting the GUI:

```
python -m stt --check
```

Also prints the command hotkey, whether voice commands and the system
whitelist are on, and how many apps, folders and system actions were found.

## How it behaves

| You do | You get |
| --- | --- |
| Hold `Ctrl+Alt+Space` | Tray icon turns red, overlay shows a live level meter |
| Speak | Audio is captured at 16 kHz mono |
| Release | Audio goes to Groq Whisper, transcript is typed into the focused window |
| Long transcript | Pasted via clipboard instead of typed char-by-char |
| Hold `Ctrl+Alt+O` | Tray icon turns blue |
| Say "open Chrome" / "close Notepad" / "lock the screen" | The app runs, closes, or the screen locks — and nothing is typed |

Short text is typed with `SendInput` Unicode events, so accented letters and
CJK work and the clipboard is left alone. Anything over 200 characters is
pasted with Ctrl+V, because per-character injection is slow and gets dropped
by heavier apps. Either way the transcript is also copied to the clipboard as
a backup you can toggle off.

If you release the key without saying anything audible, the app says so and
does not insert stray text.

## Accuracy settings

`whisper-large-v3` at `temperature=0` is the accuracy baseline — no sampling,
so repeated takes of the same audio give the same text. The tray menu switches
to `whisper-large-v3-turbo` if you want lower latency over a small accuracy
drop.

A **prompt** field primes the model with context. Useful when you dictate
proper nouns, code identifiers, or a language the default settings get wrong.
It lives in `config.json` as `prompt`:

```json
"prompt": "GitHub, Kubernetes, TypeScript. Casual technical dictation."
```

Language is pinned per session in the tray menu; set it to the right code
(`en`, `pt`, `es`, ...) instead of `auto` to stop the model from
mis-detecting short utterances.

## Command with Voice

A second hotkey acts on what you said instead of typing it. Turn it on with
**Command with Voice** in the tray menu, then:

1. Hold `Ctrl+Alt+O` (tray icon turns blue).
2. Say one of the things below.
3. Release. It happens, and nothing is typed into the focused window — the page
   you were reading keeps its content.

English and Portuguese both work, mixed freely.

| Say | What happens |
| --- | --- |
| `open Chrome`, `abra o Spotify`, `launch Visual Studio Code` | The app starts |
| `open notepad`, `open calc`, `open code` | Anything on `PATH` starts |
| `open folder Downloads`, `abrir a pasta Videos` | Explorer opens that folder |
| `open Downloads` | Same — a folder is the fallback when no app matches |
| `open youtube.com`, `open www.example.com` | Opens in the default browser |
| `close Chrome`, `feche o bloco de notas`, `quit notepad` | Every window whose title matches is closed |
| `close the folder Downloads` | Closes the Explorer window showing it |
| `lock the screen`, `travar a tela` | Workstation locks |
| `sleep`, `dormir` | Suspends |
| `empty the recycle bin`, `esvaziar a lixeira` | Empties it |
| `volume up` / `volume down` / `mute` / `unmute` | Media keys, broadcast |
| `take a screenshot`, `captura de tela` | PNG into `Pictures\Screenshots` |
| `show desktop`, `mostrar a area de trabalho` | Minimizes everything |
| `clear the clipboard` | Empties it |

The overlay says what it did, so a misheard word is visible immediately.

### What it will not do

There is no way to run an arbitrary command line, and the whitelist has no
shutdown, restart, delete or format entry. Every system action is reversible
and is spelled out in `SYSTEM_ACTIONS` in `stt/voice_command.py` — add to that
table if you want more, but read the reasoning first.

Turning **Allow system commands** off in the tray menu leaves apps, folders,
URLs and window closing, and refuses the rest with an explicit message rather
than falling through to an app lookup.

### How the utterance is read

The transcript is split into a verb and a target. Open verbs: `open`, `open up`,
`launch`, `start`, `go to`, `run`, `abra`, `abrir`, `abre`, `inicie`,
`iniciar`, `rode`, `executar`, `chame`. Close verbs: `close`, `quit`, `exit`,
`kill`, `feche`, `fechar`, `fecha`, `encerrar`, `sai`, `sair`, `terminar`.
Whitelisted system phrases stand alone — `lock the screen` has no verb at all.

Filler is stripped before matching: a leading `can you` / `por favor`, an
article (`the`, `o`, `my`), a trailing `please` / `agora`, and nouns that add
nothing. So `open the chrome browser please` still opens Chrome, and
`open up the notepad, please` still opens Notepad. Accents and punctuation are
ignored for matching, so `Calculadora` and `calculadora!` are one request — but
a spoken **path keeps its case**, so `open folder "C:\Users\Example\Documents"`
works.

If the utterance has no verb and is not a system phrase, nothing runs — the
overlay says so instead of guessing.

### How an app name is resolved

In order, first hit wins:

1. An alias from `command_aliases` in `config.json`.
2. An executable on `PATH` — so `notepad`, `calc` and `code` work even though
   they are not Start Menu entries.
3. An exact Start Menu / Store shortcut name.
4. A shortcut whose name starts with what you said, or contains it as a whole
   word — `chrome` finds `Google Chrome`.
5. The closest spelling by edit distance, above a confidence floor. `spotfy`
   opens Spotify; `autocad` opens nothing, because being wrong is worse than
   asking again.

Only if all five miss does it try the folder index.

The Start Menu is walked once and cached for two minutes. Uninstallers,
repair entries and `pip`-style console shims are skipped, and Start Menu
shortcuts outrank raw `.exe` files found under `%LOCALAPPDATA%\Programs`.

When a name does not resolve, the overlay says so and offers the closest
matches it found — `Did you mean Notepad or WordPad?`

### How a folder name is resolved

An explicit path is used as given. Otherwise the name is matched against your
shell folders walked **four levels deep**, plus a shallow pass over
`Program Files` — same exact → prefix → word → fuzzy ladder as apps.

Depth matters more than it looks. Real project folders nest
(`Downloads\organizar\projetos\Speech-To-Text`), so a one-level walk only ever
finds the container and reports the project as missing. `AppData`,
`node_modules`, `venv` and similar are skipped, and the shallowest match wins so
a top-level folder beats a nested namesake.

`%LOCALAPPDATA%` is deliberately not searched: it is mostly caches, and listing
it made "open Speech-To-Text" land in this app's own config directory instead
of the project. Installed software is already covered by the Start Menu index.

The walk is about 0.15 s and happens on a background thread at startup, so the
first spoken command never waits for it. Results are cached for two minutes.

### How a window is matched for closing

Visible top-level window titles, normalized the same way: exact title, then
prefix, then a whole word inside the title, then a close spelling. Every match
is closed, so "close Chrome" closes all of its windows. Closing posts `WM_CLOSE`,
which is exactly what the title-bar X does, so an app with unsaved work still
gets to ask. Nothing is ever force-killed, and windows belonging to this app
are excluded so a fuzzy hit can never close the overlay.

### Aliases

Windows names do not always match how people speak, and on a Portuguese
install "explorador de arquivos" is `File Explorer` in the Start Menu. Give the
word you actually say a target:

```json
"command_aliases": {
  "browser": "chrome",
  "planilha": "excel",
  "terminal": "Windows Terminal"
}
```

An alias value is an app name, resolved the same way — not a file path.

## Hotkey

Default is `Ctrl+Alt+Space`. Change it from the tray menu —
**Set dictation hotkey...** opens a dialog that waits for you to press the
combo; the binding is saved to `config.json` and takes effect immediately, no
restart.

Or at launch:

```
python -m stt --hotkey "ctrl+shift+m"
```

Only one non-modifier key, so no chords like `ctrl+alt+shift+space`. The
combo is saved to `config.json` and reused on the next start.

**Set command hotkey...** does the same for the `Ctrl+Alt+O` combo, which also
has a `--command-hotkey` flag:

```
python -m stt --command-hotkey "ctrl+win+j"
```

The two cannot be set to the same combo, since both listeners would fire on one
physical press. Only one non-modifier key per combo.

Letter triggers work with modifiers held: Windows reports `Ctrl+Alt+O` with a
mangled character and no key name, so the matcher falls back to the virtual
key code. That also covers layouts where AltGr rewrites the letter.

## Autostart

Tray → `Start with Windows` writes `HKCU\...\Run`. It launches `pythonw.exe`
when available so nothing flashes a console at login.

## Config

`%APPDATA%\Speech-To-Text\config.json`, created on first run:

| Key | Default | Meaning |
| --- | --- | --- |
| `hotkey` | `["ctrl","alt","space"]` | Hold-to-talk combo |
| `command_hotkey` | `["ctrl","alt","o"]` | Combo for the voice-command mode |
| `command_with_voice` | `true` | Master on/off for voice commands |
| `command_system_enabled` | `true` | Allow the whitelisted system actions |
| `command_aliases` | `{}` | Spoken name → app name, e.g. `{"browser":"chrome"}` |
| `model` | `whisper-large-v3` | Groq model id |
| `language` | `en` | Language code, or `auto` |
| `prompt` | `""` | Context priming for the model |
| `max_seconds` | `120` | Hard cap per recording |
| `min_rms` | `0.0035` | Below this, treated as silence |
| `auto_type` | `true` | Master on/off switch |
| `clipboard_backup` | `true` | Also copy each transcript |
| `trailing_space` | `true` | Append a space after each insertion |
| `type_delay_ms` | `6` | Delay between injected characters |
| `sample_rate` | `16000` | Capture rate (Whisper's native rate) |

Unknown or invalid values are repaired on load, so a typo in the JSON will not
stop the app from starting. A file saved with a byte-order mark (what Notepad
writes) is read fine.

Older configs migrate themselves: `open_hotkey`, `open_with_voice` and
`open_aliases` are renamed to the `command_*` keys on first load, keeping their
values, and the file is rewritten so the old names stop coming back. A key you
already set by hand always wins over the legacy one.

## Tests

```
python -m pytest tests -q
```

256 tests: hotkey state machine (key repeat, stray modifiers, lost key-ups,
Alt-modified letter keys, two coexisting combos), config repair, legacy-key
migration and BOM tolerance, WAV encoding, the Groq request shape and error
mapping, delivery routing, the silence guard, command parsing (open, close,
folder, URL and system phrases, in two languages), app / folder / window
resolution, folder index depth and budget, launch and close routing, the
whitelist guard, the Win32 window helpers, the hand-rolled PNG encoder, hotkey
capture from the tray, and the full press → transcribe → deliver pipeline
against a real Qt event loop.

The pipeline tests need a Qt platform plugin. On a headless machine:

```
set QT_QPA_PLATFORM=offscreen
```

`tools/verify_typing.py` is a manual end-to-end check that injected text
actually lands in a real focused window. It creates a bare Win32 window with an
EDIT control, injects through `stt.typing`, and compares what arrived. Run it
detached or a console will steal focus and corrupt the result:

```
Start-Process -WindowStyle Hidden python -ArgumentList tools\verify_typing.py
```

Screenshot capture is the other thing worth checking by hand, since it cannot
be asserted against a fake device context:

```
python -c "from stt import screen; print(screen.save_screenshot())"
```

## Known limits

- Detection is a low-level key hook, so the app needs to be running; there is
  no Windows service or elevated UAC prompt.
- Recording stops on the first key-up of the trigger *or* of any modifier, so
  lifting Ctrl early ends the hold rather than recording silence.
- A recording is force-stopped at `max_seconds` in case a key-up is lost to a
  focus change or session lock. A stuck overlay is worse than a long dictation
  being cut.
- Injecting into elevated windows (running as administrator) fails silently
  from a non-elevated process. Start the app elevated if you dictate into
  admin terminals or installers.
- Very high DPI scaling can blur the overlay; it renders at the logical
  resolution Qt reports.
- Whisper transcribes speech, not formatting intent. Dictating "new line" gives
  you the words, not a newline. Pauses become punctuation via the model.
- Voice commands only see what `PATH`, the Start Menu and your own folders
  advertise. Store apps that have never been launched expose no shortcut at all,
  so an alias is the way in. It matches names, not Windows search, so a
  Portuguese install still wants English app names unless you alias them.
- Folders are found up to four levels below your shell folders. A project buried
  deeper than that needs an explicit path: `open folder C:\...\...\...`.
- Two folders with the same name resolve to the shallower one. The overlay shows
  which folder it picked, so say something more specific if it was the wrong
  one.
- Closing a window posts `WM_CLOSE`, so an app that asks to save will ask. There
  is no way to force-close a process, by design.
- `sleep` asks Windows to suspend. A machine with hibernation disabled instead
  shuts down, and that is the OS's call, not this app's.
- A screenshot is the primary monitor at native resolution, saved uncompressed
  PNG. No image library is involved, so it takes about a second.
- Audio is sent to Groq's servers. If that matters for your data, say so and
  the transcription step can be swapped for a local `faster-whisper` run
  without touching the capture or delivery code.
