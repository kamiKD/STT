"""Voice commands: turn "open Chrome" or "lock the screen" into an action.

Three pieces, kept apart so each is testable on its own:

  * ``parse_command`` - spoken text -> action + target, tolerant of the filler
    Whisper adds ("please", "the", "o", "the folder").
  * ``plan`` - target -> a concrete thing to act on. No side effects, so the
    routing decisions can be asserted without a desktop.
  * ``execute`` - performs the plan: launch a shortcut, open a folder, close a
    window, or run one entry from the system-action whitelist.

Resolution is best effort on purpose. An ambiguous app name goes to the closest
match and nothing is launched when the confidence is too low, because opening
the wrong program is worse than asking again. The whitelist has no destructive
entries at all: no shutdown, no delete, no arbitrary shell.
"""

from __future__ import annotations

import ctypes
import difflib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import screen, windows
from . import typing as inject  # clipboard helpers live with the text delivery
from .text import normalize, strip_accents

# ------------------------------------------------------------------- intents
ACTION_OPEN = "open"
ACTION_CLOSE = "close"

# Longest first at match time, so "open up" never degrades into "open" + "up".
OPEN_VERBS = (
    "open up",
    "launch",
    "start up",
    "start",
    "open",
    "go to",
    "run",
    "abra",
    "abrir",
    "abre",
    "inicie",
    "iniciar",
    "rode",
    "executar",
    "chame",
)
CLOSE_VERBS = (
    "close",
    "quit",
    "exit",
    "kill",
    "feche",
    "fechar",
    "fecha",
    "encerre",
    "encerrar",
    "encerra",
    "sai",
    "sair",
    "termine",
    "terminar",
)

# Saying "the folder" or "a pasta" pins the target to the filesystem.
FOLDER_WORDS = ("folder", "pasta", "directory", "diretorio", "directorio")

# Kinds of thing a command can act on.
KIND_APP = "app"
KIND_FOLDER = "folder"
KIND_URL = "url"
KIND_WINDOW = "window"
KIND_SYSTEM = "system"

# Whisper prepends these when the utterance is a request rather than a command.
POLITE_PREFIXES = (
    "can you",
    "could you",
    "would you",
    "please",
    "por favor",
    "i want to",
    "i need to",
    "i would like to",
    "quero",
    "gostaria de",
)

LEADING_ARTICLES = {"the", "o", "a", "os", "as", "um", "uma", "uns", "umas", "my", "minha"}
TRAILING_FILLER = {
    "please",
    "pls",
    "por favor",
    "obrigado",
    "obrigada",
    "now",
    "right now",
    "agora",
    "ja",
}
# Trailing nouns that carry no identifying information: "open the chrome
# browser" should still find Chrome.
TRAILING_NOUNS = {
    "browser",
    "app",
    "application",
    "program",
    "programa",
    "aplicativo",
    "software",
    "janela",
    "window",
}

# Directories under the Start Menu that only hold installers and repair tools.
SKIP_DIRS = {
    "uninstall",
    "uninstalls",
    "deinstall",
    "install",
    "installer",
    "repair",
    "update",
    "updater",
    "temp",
    # Console-script shims from pip and friends: not what anyone means by
    # "open <app>".
    "scripts",
    "bin",
    "sbin",
}
SKIP_NAME_PREFIX = (
    "unins",
    "readme",
    "release notes",
    "documentation",
    "help",
    "deinstall",
    "product keys",
)
LAUNCHABLE_EXT = (".lnk", ".url", ".exe")

# Tier 0 is what a person means by "an app": Start Menu entries and Store
# aliases. Tier 1 is whatever else is under %LOCALAPPDATA%\Programs, searched
# last so a dev tool never outranks a real shortcut.
TIER_SHORTCUT = 0
TIER_PROGRAMS = 1

INDEX_TTL = 120.0
MATCH_MIN_SCORE = 0.68
# "Did you mean" is held to a higher bar than launching. Measured on real
# pairs: genuine slips land at 0.77-1.00 (downlods->Downloads 0.94,
# notped->Notepad 0.77), while unrelated names that squeak past at 0.35-0.56
# (Projetos->"3D Objects" 0.56) are worse than no suggestion at all.
SUGGEST_CUTOFF = 0.6

# Shell folders worth walking, plus the extra roots an app is installed under.
FOLDER_ROOTS = (
    "",
    "Pictures",
    "Documents",
    "Videos",
    "Music",
    "Desktop",
    "Downloads",
)
# %LOCALAPPDATA% is deliberately absent. It is mostly caches and app internals,
# and listing it made "open <project>" resolve to a config directory instead of
# the project, because those paths are shorter. Installed software from that
# tree is already reachable through the Start Menu index.
FOLDER_EXTRA_ROOTS = (
    r"%ProgramFiles%",
    r"%ProgramFiles(x86)%",
)
# Real project folders nest: Downloads\organizar\projetos\Speech-To-Text is
# four levels down. One level only ever found the container, never the thing.
FOLDER_MAX_DEPTH = 4
FOLDER_LIMIT = 3000
# Installed software is already covered by the Start Menu index, and a dev
# machine can bury thousands of directories under %LOCALAPPDATA%. The program
# roots therefore get a shallow walk and whatever budget the personal roots did
# not use, so a cache directory can never crowd out someone's own projects.
FOLDER_PROGRAM_DEPTH = 2
# Build trees and OS internals are never what someone means by "open folder".
FOLDER_SKIP_DIRS = {
    "appdata",
    "node_modules",
    ".git",
    "venv",
    ".venv",
    "__pycache__",
    "library",
    "temp",
    "tmp",
    "target",
    "obj",
    "bin",
    "sbin",
    ".cache",
    "programs",
    "package cache",
    "windowsapps",
    "$recycle.bin",
    "system volume information",
}

# A bare word with a dot in it and no space is treated as a web address.
URL_RE = re.compile(r"^[a-z0-9][a-z0-9-]*(\.[a-z0-9-]+)+(/.*)?$")


@dataclass(frozen=True)
class VoiceCommand:
    """A parsed utterance: what to do, to what, and how it was said."""

    action: str  # ACTION_OPEN | ACTION_CLOSE
    verb: str
    target: str
    kind: str  # "" when the speaker did not say, resolved later
    raw: str


@dataclass(frozen=True)
class AppEntry:
    """One launchable thing found on the machine."""

    name: str  # display name, e.g. "Google Chrome"
    path: str  # full path to the .lnk / .exe
    tier: int = TIER_SHORTCUT  # 0 = Start Menu / Store, 1 = Programs dir


@dataclass(frozen=True)
class FolderEntry:
    """One directory worth opening."""

    name: str
    path: str


@dataclass(frozen=True)
class Resolution:
    """Outcome of a lookup: what to act on, and where to find it."""

    ok: bool
    name: str = ""
    target: str = ""
    kind: str = ""
    message: str = ""
    suggestions: list[str] = field(default_factory=list)
    handles: tuple[int, ...] = ()  # window handles, for KIND_WINDOW


# --------------------------------------------------------------------- text
def _loose(text: str) -> str:
    """Collapse whitespace and drop accents, but keep case and punctuation.

    normalize() would turn "C:\\Users\\Ana" into "c users ana", which no longer
    opens anything. Paths and URLs are read from this instead.
    """
    return re.sub(r"\s+", " ", strip_accents(text)).strip()


def _clean_target(target: str) -> str:
    """Trim the parts of a name that the speaker adds but the OS does not know."""
    words = normalize(target).split()
    if words and words[0] in LEADING_ARTICLES:
        words = words[1:]
    return " ".join(_drop_filler(words))


def _drop_filler(words: list[str]) -> list[str]:
    """Peel trailing 'please' / 'por favor' / 'agora' off a word list."""
    while len(words) > 1 and " ".join(normalize(w) for w in words[-2:]) in TRAILING_FILLER:
        words = words[:-2]
    while len(words) > 1 and normalize(words[-1]) in TRAILING_FILLER | TRAILING_NOUNS:
        words = words[:-1]
    return words


def _strip_target(target: str) -> str:
    """Clean an app name, or leave an address alone."""
    if looks_like_url(target):
        return _loose(target).lower().strip(" .!?,;")
    return _clean_target(target)


def parse_command(text: str) -> VoiceCommand | None:
    """Split "abra a pasta Downloads, por favor" into action, verb and target.

    Returns None when the utterance has no open or close verb and is not itself
    a system phrase, which is the signal that this was dictation.
    """
    raw = str(text or "").strip()
    # Words keep their original case so a spoken path survives intact; the
    # folded form is derived word by word and never replaces them.
    words = _loose(raw).split()
    norm = " ".join(normalize(word) for word in words)

    # "Can you please open X" carries two prefixes, so keep going until none
    # of them match, dropping the same words from both views each time.
    changed = True
    while changed:
        changed = False
        for prefix in POLITE_PREFIXES:
            if norm.startswith(prefix + " "):
                count = len(prefix.split())
                norm = norm[len(prefix) + 1:]
                words = words[count:]
                changed = True

    if not norm:
        return None

    verbs = [(verb, ACTION_OPEN) for verb in OPEN_VERBS]
    verbs += [(verb, ACTION_CLOSE) for verb in CLOSE_VERBS]
    for verb, action in sorted(verbs, key=lambda pair: len(pair[0]), reverse=True):
        if norm == verb:
            return VoiceCommand(action, verb, "", "", raw)
        if not norm.startswith(verb + " "):
            continue

        tail_norm = norm[len(verb) + 1:]
        tail = words[len(verb.split()):]
        if tail and tail_norm.split()[0] in LEADING_ARTICLES:
            tail = tail[1:]

        # "open the folder Downloads" pins the target to the filesystem.
        if tail and normalize(tail[0]) in FOLDER_WORDS:
            if len(tail) == 1:
                return VoiceCommand(action, verb, "", KIND_FOLDER, raw)
            target = " ".join(_drop_filler(tail[1:])).strip(' "')
            return VoiceCommand(action, verb, target, KIND_FOLDER, raw)

        return VoiceCommand(action, verb, _strip_target(" ".join(tail)), "", raw)

    # A whitelisted system phrase stands on its own: "lock the screen" has no
    # open or close verb, but it is still a command.
    if SYSTEM_LOOKUP.get(norm) is not None:
        return VoiceCommand(ACTION_OPEN, "", norm, KIND_SYSTEM, raw)
    return None


def looks_like_url(target: str) -> bool:
    text = _loose(target).lower()
    if " " in text:
        return False
    if text.startswith(("http://", "https://", "www.")):
        return True
    return bool(URL_RE.match(text))


def as_url(target: str) -> str:
    text = str(target).strip()
    if text.startswith(("http://", "https://")):
        return text
    if text.startswith("www."):
        return "https://" + text
    return "https://" + text


# ---------------------------------------------------------------- app index
def search_roots() -> list[tuple[Path, int]]:
    """Where installed programs advertise themselves, best tier first."""
    start_menu = Path("Microsoft") / "Windows" / "Start Menu" / "Programs"
    roots: list[tuple[Path, int]] = []
    appdata = os.environ.get("APPDATA")
    programdata = os.environ.get("ProgramData")
    local = os.environ.get("LOCALAPPDATA")
    if appdata:
        roots.append((Path(appdata) / start_menu, TIER_SHORTCUT))
    if programdata:
        roots.append((Path(programdata) / start_menu, TIER_SHORTCUT))
    if local:
        roots.append((Path(local) / "Microsoft" / "Windows" / "WindowsApps", TIER_SHORTCUT))
        roots.append((Path(local) / "Programs", TIER_PROGRAMS))
    return roots


def iter_launchables(roots=None, limit: int = 4000):
    """Yield (display name, path, tier) for every shortcut or exe under `roots`."""
    seen = 0
    if roots is None:
        located = search_roots()
    else:
        located = [(Path(root), TIER_SHORTCUT) for root in roots]
    for root, tier in located:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")
            ]
            for filename in filenames:
                stem, ext = os.path.splitext(filename)
                if ext.lower() not in LAUNCHABLE_EXT:
                    continue
                stem = stem.strip()
                if not stem or stem.lower().startswith(SKIP_NAME_PREFIX):
                    continue
                yield stem, os.path.join(dirpath, filename), tier
                seen += 1
                if seen >= limit:
                    return


def build_index(roots=None, limit: int = 4000) -> list[AppEntry]:
    """Deduplicated list of launchables, one entry per normalized name."""
    best: dict[str, AppEntry] = {}
    for name, path, tier in iter_launchables(roots, limit=limit):
        key = normalize(name)
        if not key:
            continue
        entry = AppEntry(name=name, path=path, tier=tier)
        current = best.get(key)
        if current is None or (entry.tier, len(path)) < (current.tier, len(current.path)):
            best[key] = entry
    return sorted(best.values(), key=lambda e: (e.tier, e.name.lower()))


_index_cache: list[AppEntry] | None = None
_index_stamp = 0.0
_folder_cache: list[FolderEntry] | None = None
_folder_stamp = 0.0


def _stale(stamp: float) -> bool:
    return time.monotonic() - stamp > INDEX_TTL


def cached_index() -> list[AppEntry]:
    """App index rebuilt at most every INDEX_TTL seconds; disk walking is slow."""
    global _index_cache, _index_stamp
    if _index_cache is None or _stale(_index_stamp):
        _index_cache = build_index()
        _index_stamp = time.monotonic()
    return _index_cache


def cached_folders() -> list[FolderEntry]:
    """One level of subdirectories under the places people keep their work."""
    global _folder_cache, _folder_stamp
    if _folder_cache is None or _stale(_folder_stamp):
        _folder_cache = build_folder_index()
        _folder_stamp = time.monotonic()
    return _folder_cache


def clear_cache() -> None:
    global _index_cache, _index_stamp, _folder_cache, _folder_stamp
    _index_cache = None
    _index_stamp = 0.0
    _folder_cache = None
    _folder_stamp = 0.0


def _walk_dirs(roots, max_depth: int, limit: int) -> list[tuple[int, str, str]]:
    """(depth, name, path) for every directory up to `max_depth` below `roots`."""
    rows: list[tuple[int, str, str]] = []
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for dirpath, dirnames, _ in os.walk(root):
            here = len(Path(dirpath).relative_to(root).parts)
            if here >= max_depth:
                dirnames[:] = []  # one level too deep: do not even look
                continue
            dirnames[:] = [
                d
                for d in dirnames
                if d.lower() not in FOLDER_SKIP_DIRS and not d.startswith(".")
            ]
            for name in dirnames:
                rows.append((here + 1, name, os.path.join(dirpath, name)))
            if len(rows) >= limit:
                return rows
    return rows


def build_folder_index(
    roots=None,
    limit: int = FOLDER_LIMIT,
    max_depth: int = FOLDER_MAX_DEPTH,
    program_depth: int = FOLDER_PROGRAM_DEPTH,
) -> list[FolderEntry]:
    """Directories below the shell folders, shallowest match first.

    Depth is the whole point: "open projetos" has to reach
    Downloads\\organizar\\projetos, not stop at the container. The personal
    folders are walked first and deeply; the program roots get a shallow walk
    with the leftover budget, so %LOCALAPPDATA% cannot crowd them out.
    """
    if roots is None:
        home = Path(os.path.expanduser("~"))
        personal = [home / relative if relative else home for relative in FOLDER_ROOTS]
        program = [Path(os.path.expandvars(p)) for p in FOLDER_EXTRA_ROOTS]
    else:
        personal, program = [Path(root) for root in roots], []

    rows = _walk_dirs(personal, max_depth, limit)
    rows += _walk_dirs(program, program_depth, max(0, limit - len(rows)))

    # Shallowest first, then shortest path: a top-level folder has to beat a
    # nested namesake, otherwise "open projetos" picks whichever won the race.
    rows.sort(key=lambda row: (row[0], len(row[2]), row[1].lower()))
    seen: set[str] = set()
    out: list[FolderEntry] = []
    for _, name, path in rows:
        key = normalize(name)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(FolderEntry(name=name, path=path))
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ resolve
def _match_entry(
    target: str, by_key: dict[str, AppEntry], keys: list[str], min_score: float
) -> AppEntry | None:
    """Exact name, then prefix, then whole-word containment, then fuzzy.

    `keys` arrives tiered, so a shortcut always outranks a dev tool with a
    similar name.
    """
    entry = by_key.get(target)
    if entry is not None:
        return entry
    if len(target) < 3:
        return None
    prefixed = sorted(
        (k for k in by_key if k.startswith(target)), key=lambda k: (len(k), k)
    )
    if prefixed:
        return by_key[prefixed[0]]
    words = sorted(
        (k for k in by_key if target in k.split()), key=lambda k: (len(k), k)
    )
    if words:
        return by_key[words[0]]
    close = difflib.get_close_matches(target, keys, n=1, cutoff=min_score)
    if close:
        return by_key[close[0]]
    return None


def suggest(target: str, names, limit: int = 3, cutoff: float = SUGGEST_CUTOFF) -> list[str]:
    """Closest names to `target`, as they are displayed."""
    pairs = [(normalize(name), name) for name in names]
    display = dict(pairs)
    close = difflib.get_close_matches(
        normalize(target), [key for key, _ in pairs], n=limit, cutoff=cutoff
    )
    return [display[key] for key in close]


def resolve_app(
    name: str, *, index=None, aliases=None, min_score: float = MATCH_MIN_SCORE
) -> Resolution:
    """Find the best match for a spoken app name. Never launches anything."""
    target = normalize(name)
    if not target:
        return Resolution(ok=False, message='Say the app name, e.g. "open Chrome".')

    entries = cached_index() if index is None else index
    keys = [normalize(e.name) for e in entries]
    by_key = {normalize(e.name): e for e in entries}

    if aliases:
        for alias, value in aliases.items():
            if normalize(alias) != target:
                continue
            aliased = _lookup(shutil.which(str(value)), normalize(value), by_key, keys, min_score)
            if aliased is None:
                return Resolution(
                    ok=False,
                    kind=KIND_APP,
                    message=f'Alias "{alias}" points at "{value}", which is not installed.',
                )
            return Resolution(ok=True, name=aliased.name, target=aliased.path, kind=KIND_APP)

    found = _lookup(shutil.which(target), target, by_key, keys, min_score)
    if found is not None:
        return Resolution(ok=True, name=found.name, target=found.path, kind=KIND_APP)
    return Resolution(
        ok=False,
        kind=KIND_APP,
        message=f'"{name}" is not installed.',
        suggestions=suggest(name, [e.name for e in entries]),
    )


def _lookup(on_path, target: str, by_key, keys, min_score) -> AppEntry | None:
    if on_path:
        # Display the command without its extension: "notepad", not
        # "notepad.EXE".
        stem = os.path.splitext(os.path.basename(on_path))[0]
        return AppEntry(name=stem, path=on_path)
    return _match_entry(target, by_key, keys, min_score)


def resolve_folder(name: str, *, entries=None, min_score: float = MATCH_MIN_SCORE) -> Resolution:
    """Find a directory from what the speaker said.

    An explicit path wins outright. Otherwise the spoken name is matched
    against the shell folders and one level of their children.
    """
    raw = str(name or "").strip().strip('"').strip("'")
    if not raw:
        return Resolution(ok=False, kind=KIND_FOLDER, message="Say which folder.")

    expanded = os.path.expandvars(os.path.expanduser(raw))
    if os.path.isdir(expanded):
        return Resolution(
            ok=True, name=os.path.basename(expanded.rstrip("\\/")) or expanded,
            target=expanded, kind=KIND_FOLDER,
        )

    target = normalize(raw)
    entries = cached_folders() if entries is None else list(entries)
    by_key = {normalize(e.name): e for e in entries}
    keys = list(by_key)

    entry = by_key.get(target)
    if entry is None and len(target) >= 3:
        prefixed = sorted((k for k in by_key if k.startswith(target)), key=lambda k: len(k))
        entry = by_key[prefixed[0]] if prefixed else None
    if entry is None:
        words = sorted((k for k in by_key if target in k.split()), key=lambda k: len(k))
        entry = by_key[words[0]] if words else None
    if entry is None:
        close = difflib.get_close_matches(target, keys, n=1, cutoff=min_score)
        entry = by_key[close[0]] if close else None

    if entry is not None:
        return Resolution(ok=True, name=entry.name, target=entry.path, kind=KIND_FOLDER)
    return Resolution(
        ok=False,
        kind=KIND_FOLDER,
        message=f'No folder called "{raw}".',
        suggestions=suggest(raw, [e.name for e in entries]),
    )


def resolve_window(name: str, *, wins=None, exclude_pid: int | None = None) -> Resolution:
    """Find the open windows whose title matches what the speaker said."""
    target = normalize(name)
    if not target:
        return Resolution(ok=False, kind=KIND_WINDOW, message="Say what to close.")
    found = windows.match_windows(target, windows=wins, exclude_pid=exclude_pid)
    if not found:
        pool = wins if wins is not None else windows.list_windows()
        return Resolution(
            ok=False,
            kind=KIND_WINDOW,
            message=f'No open window called "{name}".',
            suggestions=suggest(name, [w.title for w in pool]),
        )
    if len(found) == 1:
        name_out = found[0].title
    else:
        name_out = f"{found[0].title} (+{len(found) - 1} more)"
    return Resolution(
        ok=True,
        name=name_out,
        target=found[0].title,
        kind=KIND_WINDOW,
        handles=tuple(w.hwnd for w in found),
    )


# ------------------------------------------------------- system action table
SYSTEM_LABELS = {
    "lock": "Locking the screen",
    "sleep": "Going to sleep",
    "empty_recycle_bin": "Emptying the recycle bin",
    "mute": "Muting",
    "unmute": "Unmuting",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "show_desktop": "Showing the desktop",
    "screenshot": "Taking a screenshot",
    "clear_clipboard": "Clearing the clipboard",
}

# Reversible actions only. There is deliberately no shutdown, no restart and no
# way to run an arbitrary command line: a misheard word must not be able to end
# a session or destroy a file.
SYSTEM_ACTIONS = {
    "lock": (
        "lock",
        "lock the screen",
        "lock the computer",
        "lock my computer",
        "lock this computer",
        "travar a tela",
        "travar o computador",
        "bloquear a tela",
        "bloquear o computador",
    ),
    "sleep": (
        "sleep",
        "go to sleep",
        "sleep the computer",
        "put the computer to sleep",
        "dormir",
        "ir dormir",
        "suspender",
        "suspender o computador",
    ),
    "empty_recycle_bin": (
        "empty the recycle bin",
        "empty recycle bin",
        "clear the recycle bin",
        "clean the recycle bin",
        "esvaziar a lixeira",
        "limpar a lixeira",
    ),
    "mute": (
        "mute",
        "mute the volume",
        "mute the sound",
        "silence the sound",
        "mudo",
        "silenciar",
        "mudo o som",
        "silenciar o som",
    ),
    "unmute": (
        "unmute",
        "unmute the volume",
        "unmute the sound",
        "turn the sound on",
        "tirar o mudo",
        "desmudo",
        "ativar o som",
        "ligar o som",
    ),
    "volume_up": (
        "volume up",
        "turn the volume up",
        "raise the volume",
        "increase the volume",
        "louder",
        "aumentar o volume",
        "aumentar o som",
    ),
    "volume_down": (
        "volume down",
        "turn the volume down",
        "lower the volume",
        "decrease the volume",
        "quieter",
        "diminuir o volume",
        "baixar o som",
    ),
    "show_desktop": (
        "show desktop",
        "show the desktop",
        "minimize everything",
        "show my desktop",
        "mostrar a area de trabalho",
        "mostrar a mesa",
        "minimizar tudo",
    ),
    "screenshot": (
        "screenshot",
        "take a screenshot",
        "capture the screen",
        "take a screen shot",
        "captura de tela",
        "tirar print",
        "capturar a tela",
    ),
    "clear_clipboard": (
        "clear the clipboard",
        "empty the clipboard",
        "wipe the clipboard",
        "limpar a area de transferencia",
    ),
}


def _system_lookup() -> dict[str, str]:
    table: dict[str, str] = {}
    for canonical, phrases in SYSTEM_ACTIONS.items():
        for phrase in phrases:
            table.setdefault(normalize(phrase), canonical)
    return table


SYSTEM_LOOKUP = _system_lookup()


def resolve_system(name: str) -> Resolution | None:
    """Match a system action, or None when this is not one."""
    target = normalize(name)
    canonical = SYSTEM_LOOKUP.get(target)
    if canonical is None:
        words = target.split()
        if words and words[0] in LEADING_ARTICLES:
            canonical = SYSTEM_LOOKUP.get(" ".join(words[1:]))
    if canonical is None:
        return None
    return Resolution(
        ok=True,
        name=SYSTEM_LABELS[canonical],
        target=canonical,
        kind=KIND_SYSTEM,
    )


def system_suggestions(name: str, limit: int = 3, cutoff: float = 0.62) -> list[str]:
    """System actions close enough to be worth naming. Tighter than the app
    cutoff: "lock the screen" is worth offering, unrelated speech is not."""
    return suggest(name, list(SYSTEM_LABELS.values()), limit=limit, cutoff=cutoff)


def perform_system(canonical: str) -> str:
    """Run one whitelisted action. Raises OSError when Windows refuses."""
    if canonical == "lock":
        if not windows.user32.LockWorkStation():
            raise OSError("Windows refused to lock the workstation")
    elif canonical == "sleep":
        # ctypes.WinDLL is loaded on demand: powrprof is only needed here.
        powrprof = ctypes.WinDLL("powrprof")
        # Hibernate=0, ForceCritical=0, WakeupEventsDisabled=1
        if powrprof.SetSuspendState(0, 0, 1) == 0:
            raise OSError("Windows refused to suspend")
    elif canonical == "empty_recycle_bin":
        shell32 = ctypes.WinDLL("shell32")
        if shell32.SHEmptyRecyclerW(None) != 0:
            raise OSError("the recycle bin could not be emptied")
    elif canonical in ("mute", "unmute"):
        # The media key is a toggle, so there is one command for both.
        windows.broadcast(windows.APPCOMMAND_VOLUME_MUTE)
    elif canonical == "volume_up":
        windows.broadcast(windows.APPCOMMAND_VOLUME_UP)
    elif canonical == "volume_down":
        windows.broadcast(windows.APPCOMMAND_VOLUME_DOWN)
    elif canonical == "show_desktop":
        windows.show_desktop()
    elif canonical == "screenshot":
        return str(screen.save_screenshot())
    elif canonical == "clear_clipboard":
        if not inject.clear_clipboard():
            raise OSError("the clipboard was busy")
    else:  # pragma: no cover - defensive
        raise ValueError(f"unknown system action: {canonical}")
    return canonical


# --------------------------------------------------------------------- plan
def plan(
    command: VoiceCommand,
    *,
    index=None,
    folders=None,
    aliases=None,
    system_enabled: bool = True,
    wins=None,
) -> Resolution:
    """Work out what to do, without doing it."""
    target = command.target

    # "lock the screen" arrives pre-resolved: the verb is the whole phrase.
    if command.kind == KIND_SYSTEM:
        if system_enabled:
            return resolve_system(target)
        return Resolution(
            ok=False,
            kind=KIND_SYSTEM,
            message="System commands are turned off in the tray menu.",
        )

    if not target:
        return Resolution(ok=False, message='Say what to do, e.g. "open Chrome".')

    if command.action == ACTION_CLOSE:
        return resolve_window(target, wins=wins)

    if system_enabled:
        system = resolve_system(target)
        if system is not None:
            return system

    if looks_like_url(target):
        url = as_url(target)
        return Resolution(ok=True, name=target, target=url, kind=KIND_URL)

    if command.kind == KIND_FOLDER or os.path.isdir(os.path.expandvars(os.path.expanduser(target))):
        folder = resolve_folder(target, entries=folders)
        if folder.ok:
            return folder
        if command.kind == KIND_FOLDER:
            return folder

    app = resolve_app(target, index=index, aliases=aliases)
    if app.ok:
        return app

    # "open Downloads" with no "folder" in it still means the folder: the app
    # lookup only fails once nothing by that name is installed.
    fallback = resolve_folder(target, entries=folders)
    if fallback.ok:
        return fallback
    return app


def execute(command: VoiceCommand, **kwargs) -> Resolution:
    """Plan the command and carry it out."""
    result = plan(command, **kwargs)
    if not result.ok:
        return result
    try:
        if result.kind == KIND_SYSTEM:
            perform_system(result.target)
        elif result.kind == KIND_WINDOW:
            if not windows.close_windows(result.handles):
                raise OSError("the window refused to close")
        else:
            launch(result.target)
    except (OSError, ValueError) as exc:
        return Resolution(
            ok=False, name=result.name, kind=result.kind, message=f"{result.name}: {exc}"
        )
    return result


def launch(target: str) -> None:
    """Start `target` the way Explorer would, so working dir and args survive."""
    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        startfile(target)
        return
    subprocess.Popen([target], close_fds=True)  # pragma: no cover - non-Windows


def execute_text(text: str, **kwargs) -> Resolution:
    """Parse and run in one step. This is what the tray menu calls."""
    command = parse_command(text)
    if command is not None:
        return execute(command, **kwargs)

    message = f'Not a command: "{text}". Try "open Chrome" or "lock the screen".'
    # A near miss on a system phrase is worth naming: "trava a tela" for a
    # slurred "lock the screen" should not just fail.
    near = system_suggestions(text)
    return Resolution(ok=False, message=message, suggestions=near)