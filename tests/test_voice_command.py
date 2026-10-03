"""Tests for voice commands: parsing, routing, resolution and launch.

No real app, folder, window or system action is touched: the launcher, the
window list and the system table are all injected.
"""

from __future__ import annotations

import os
import threading

import pytest

from stt import text, voice_command
from stt.text import normalize
from stt.voice_command import (
    ACTION_CLOSE,
    ACTION_OPEN,
    KIND_APP,
    KIND_FOLDER,
    KIND_SYSTEM,
    KIND_URL,
    KIND_WINDOW,
)
from stt.windows import WindowInfo


# -------------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    "spoken,target",
    [
        ("Open Chrome", "chrome"),
        ("Open the Chrome browser", "chrome"),
        ("open up notepad", "notepad"),
        ("Launch Visual Studio Code", "visual studio code"),
        ("Start Spotify, please", "spotify"),
        ("open the chrome browser please", "chrome"),
        ("abra o Spotify", "spotify"),
        ("abra o navegador chrome", "navegador chrome"),
        ("Abrir o Bloco de Notas", "bloco de notas"),
        ("abre o navegador, por favor", "navegador"),
        ("Iniciar calculadora agora", "calculadora"),
    ],
)
def test_open_commands_are_understood(spoken, target):
    command = voice_command.parse_command(spoken)
    assert command.action == ACTION_OPEN
    assert command.target == target


@pytest.mark.parametrize(
    "spoken",
    [
        "close Chrome",
        "Close the Chrome window",
        "quit notepad",
        "exit the browser",
        "feche o bloco de notas",
        "encerrar o Spotify",
        "sai do explorer",
    ],
)
def test_close_commands_are_understood(spoken):
    command = voice_command.parse_command(spoken)
    assert command.action == ACTION_CLOSE
    assert command.target


def test_folder_hint_is_extracted():
    command = voice_command.parse_command("Open the folder Downloads")
    assert command.action == ACTION_OPEN
    assert command.kind == KIND_FOLDER
    # Folder targets keep the case they were said with, so a spoken path works.
    assert command.target == "Downloads"


def test_portuguese_folder_hint_is_extracted():
    command = voice_command.parse_command("Abrir a pasta Documentos")
    assert command.kind == KIND_FOLDER
    assert command.target == "Documentos"


def test_bare_folder_verb_keeps_the_kind_and_drops_the_name():
    command = voice_command.parse_command("open folder")
    assert command.kind == KIND_FOLDER
    assert command.target == ""


def test_close_keeps_the_folder_hint():
    command = voice_command.parse_command("close the folder Downloads")
    assert command.action == ACTION_CLOSE
    assert command.target == "Downloads"


def test_a_spoken_path_keeps_its_case_and_separators():
    command = voice_command.parse_command(r'open folder "C:\Users\Example\Documents"')
    assert command.target == r"C:\Users\Example\Documents"


def test_poleness_prefixes_are_stripped():
    assert voice_command.parse_command("Can you please open Firefox?").target == "firefox"


def test_a_system_phrase_needs_no_open_or_close_verb():
    command = voice_command.parse_command("lock the screen")
    assert command.kind == KIND_SYSTEM
    assert command.target == "lock the screen"
    assert voice_command.parse_command("por favor, travar a tela").kind == KIND_SYSTEM


def test_longest_verb_wins():
    assert voice_command.parse_command("open up Chrome").verb == "open up"


@pytest.mark.parametrize(
    "spoken", ["", "   ", "hello there", "the meeting is at noon", "format the disk"]
)
def test_a_phrase_without_a_command_verb_is_not_a_command(spoken):
    assert voice_command.parse_command(spoken) is None


def test_bare_verb_has_no_target():
    command = voice_command.parse_command("Open.")
    assert command.action == ACTION_OPEN
    assert command.target == ""


def test_raw_transcript_is_preserved():
    assert voice_command.parse_command("Open Chrome").raw == "Open Chrome"


# ----------------------------------------------------------------- normalize
def test_normalize_drops_accents_and_punctuation():
    assert normalize("Bloco de Notas, 2!") == "bloco de notas 2"


def test_normalize_folds_diacritics():
    assert normalize("Música") == normalize("musica")


# --------------------------------------------------------------------- index
def _write(root, *relative):
    """Create files, creating the parent directories as needed."""
    for name in relative:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    return root


def _mkdirs(root, *relative):
    """Create empty directories, which is what a folder looks like on disk."""
    for name in relative:
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def test_build_index_finds_shortcuts_and_exes(tmp_path):
    _write(tmp_path, "Google Chrome.lnk", "Spotify/Spotify.exe", "nested/deep/Notepad.lnk")
    names = {entry.name for entry in voice_command.build_index([tmp_path])}
    assert {"Google Chrome", "Spotify", "Notepad"} <= names


def test_build_index_ignores_uninstallers(tmp_path):
    _write(tmp_path, "Uninstall Thing.lnk", "uninstall/Remove Me.lnk", "Readme.txt", "Real App.lnk")
    assert {e.name for e in voice_command.build_index([tmp_path])} == {"Real App"}


def test_build_index_dedupes_by_name(tmp_path):
    _write(tmp_path, "a/Dupe.lnk", "b/Dupe.lnk")
    assert len(voice_command.build_index([tmp_path])) == 1


def test_build_index_survives_a_missing_root(tmp_path):
    assert voice_command.build_index([tmp_path / "nope"]) == []


def test_shortcut_tier_outranks_the_programs_dir(tmp_path, monkeypatch):
    _write(tmp_path, "shortcut/Tool.lnk", "programs/Tool.lnk", "programs/Only.exe")
    monkeypatch.setattr(
        voice_command,
        "search_roots",
        lambda: [
            (tmp_path / "shortcut", voice_command.TIER_SHORTCUT),
            (tmp_path / "programs", voice_command.TIER_PROGRAMS),
        ],
    )
    index = voice_command.build_index()
    tool = next(e for e in index if e.name == "Tool")
    assert tool.tier == voice_command.TIER_SHORTCUT
    assert "shortcut" in tool.path


# ------------------------------------------------------------------ app index
@pytest.fixture
def index():
    return [
        voice_command.AppEntry("Google Chrome", r"C:\menu\Google Chrome.lnk"),
        voice_command.AppEntry("Notepad", r"C:\menu\Notepad.lnk"),
        voice_command.AppEntry("Visual Studio Code", r"C:\menu\VS Code.lnk"),
        voice_command.AppEntry("Spotify", r"C:\menu\Spotify.lnk"),
    ]


@pytest.fixture
def no_path(monkeypatch):
    monkeypatch.setattr(voice_command.shutil, "which", lambda name: None)


def test_resolve_app_exact_name(index, no_path):
    result = voice_command.resolve_app("Notepad", index=index)
    assert result.ok and result.kind == KIND_APP
    assert result.name == "Notepad"


def test_resolve_app_ignores_case(index, no_path):
    assert voice_command.resolve_app("notepad", index=index).ok


def test_resolve_app_matches_one_word_of_the_name(index, no_path):
    assert voice_command.resolve_app("chrome", index=index).name == "Google Chrome"


def test_resolve_app_falls_back_to_a_close_spelling(index, no_path):
    assert voice_command.resolve_app("spotfy", index=index).name == "Spotify"


def test_resolve_app_refuses_a_confident_mismatch(index, no_path):
    result = voice_command.resolve_app("autocad", index=index)
    assert result.ok is False
    assert "is not installed" in result.message
    assert result.suggestions == []


def test_suggest_offers_a_genuine_slip():
    assert "Downloads" in voice_command.suggest("downlods", ["Downloads", "Videos"])


@pytest.mark.parametrize("junk", ["3D Objects", "CD Projet Red", "Music"])
def test_suggest_refuses_speculative_matches(junk):
    """These are what the overlay offered for 'Projetos' before the cutoff."""
    assert voice_command.suggest("Projetos", [junk]) == []


def test_suggest_keeps_display_case():
    assert voice_command.suggest("notped", ["Notepad"]) == ["Notepad"]


def test_resolve_app_prefers_path_hits(index, monkeypatch):
    monkeypatch.setattr(voice_command.shutil, "which", lambda name: r"C:\tools\notepad.exe")
    assert voice_command.resolve_app("notepad", index=index).target == r"C:\tools\notepad.exe"


# ------------------------------------------------- PATH lookup vs the cwd
def test_a_binary_in_the_cwd_cannot_shadow_an_installed_app(index, monkeypatch, tmp_path):
    """On Windows shutil.which searches the cwd first.

    That made "open chrome" launch a chrome.exe sitting in whatever directory
    the process happened to be in, instead of the indexed shortcut.
    """
    monkeypatch.chdir(tmp_path)
    planted = tmp_path / "chrome.exe"
    planted.write_bytes(b"")
    monkeypatch.setattr(voice_command.shutil, "which", lambda name: str(planted))

    result = voice_command.resolve_app("chrome", index=index)
    assert result.ok
    assert result.target == r"C:\menu\Google Chrome.lnk"


def test_a_bare_name_hit_is_treated_as_a_cwd_hit(index, monkeypatch, tmp_path):
    """which() can answer with no directory at all; that is a cwd hit too."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(voice_command.shutil, "which", lambda name: "chrome.EXE")
    assert (
        voice_command.resolve_app("chrome", index=index).target
        == r"C:\menu\Google Chrome.lnk"
    )


def test_a_real_path_hit_outside_the_cwd_still_wins(index, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        voice_command.shutil, "which", lambda name: r"C:\tools\chrome.exe"
    )
    assert voice_command.resolve_app("chrome", index=index).target == r"C:\tools\chrome.exe"


def test_an_alias_cannot_reach_a_cwd_binary(index, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    planted = tmp_path / "chrome.exe"
    planted.write_bytes(b"")
    monkeypatch.setattr(voice_command.shutil, "which", lambda name: str(planted))
    result = voice_command.resolve_app(
        "browser", index=index, aliases={"browser": "chrome"}
    )
    assert result.target == r"C:\menu\Google Chrome.lnk"


def test_the_cwd_hit_is_not_swallowed_when_nothing_else_matches(
    no_index, monkeypatch, tmp_path
):
    """With no shortcut to fall back on, the hit is still better than nothing."""
    monkeypatch.chdir(tmp_path)
    planted = tmp_path / "mytool.exe"
    planted.write_bytes(b"")
    monkeypatch.setattr(voice_command.shutil, "which", lambda name: str(planted))
    result = voice_command.resolve_app("mytool", index=[])
    assert result.ok and result.target == str(planted)


@pytest.fixture
def no_index():
    return []


def test_resolve_app_uses_an_alias(index, no_path):
    result = voice_command.resolve_app("browser", index=index, aliases={"browser": "chrome"})
    assert result.name == "Google Chrome"


def test_resolve_app_reports_a_broken_alias(index, no_path):
    result = voice_command.resolve_app("browser", index=index, aliases={"browser": "netscape"})
    assert result.ok is False
    assert "netscape" in result.message


# -------------------------------------------------------------------- folders
@pytest.fixture
def folders(tmp_path):
    _mkdirs(tmp_path, "Documents", "Downloads", "Videos")
    return voice_command.build_folder_index([tmp_path])


def test_a_nested_folder_is_indexed(tmp_path):
    """The reported failure: 'projetos' lives two levels under Downloads."""
    _mkdirs(tmp_path, "Downloads/organizar/projetos/Speech-To-Text")
    index = voice_command.build_folder_index([tmp_path])
    names = {e.name for e in index}
    assert {"organizar", "projetos", "Speech-To-Text"} <= names


def test_the_index_reaches_four_levels(tmp_path):
    _mkdirs(tmp_path, "a/b/c/d/e")
    names = {e.name for e in voice_command.build_folder_index([tmp_path])}
    assert "d" in names
    assert "e" not in names  # one level past the bound


def test_depth_is_configurable(tmp_path):
    _mkdirs(tmp_path, "a/b/c")
    shallow = voice_command.build_folder_index([tmp_path], max_depth=1)
    assert {e.name for e in shallow} == {"a"}


def test_build_trees_are_skipped(tmp_path):
    _mkdirs(tmp_path, "keep", "node_modules", "__pycache__", ".git", "AppData")
    names = {e.name for e in voice_command.build_folder_index([tmp_path])}
    assert names == {"keep"}


def test_the_shallowest_folder_wins(tmp_path):
    _mkdirs(tmp_path, "Projetos", "Downloads/organizar/Projetos")
    index = voice_command.build_folder_index([tmp_path])
    entry = next(e for e in index if e.name == "Projetos")
    assert entry.path == str(tmp_path / "Projetos")


def test_the_shortest_path_breaks_a_depth_tie(tmp_path):
    _mkdirs(tmp_path, "z/Projetos", "a/Projetos")
    index = voice_command.build_folder_index([tmp_path])
    entry = next(e for e in index if e.name == "Projetos")
    assert entry.path.endswith(os.path.join("a", "Projetos"))


def test_the_limit_is_respected(tmp_path):
    _mkdirs(tmp_path, *[f"d{i}" for i in range(20)])
    assert len(voice_command.build_folder_index([tmp_path], limit=5)) == 5


def test_local_appdata_is_not_a_folder_root():
    """It made 'open <project>' land in a config directory instead."""
    assert not any("LOCALAPPDATA" in root.upper() for root in voice_command.FOLDER_EXTRA_ROOTS)


def test_personal_roots_are_walked_before_program_roots(monkeypatch, tmp_path):
    """A program root must never use the budget the personal roots needed."""
    personal = tmp_path / "personal"
    program = tmp_path / "program"
    _mkdirs(personal, "Downloads/organizar/projetos")
    _mkdirs(program, *[f"pad{i}" for i in range(30)])

    monkeypatch.setattr(
        voice_command, "FOLDER_ROOTS", ("",), raising=False
    )
    monkeypatch.setattr(
        voice_command, "FOLDER_EXTRA_ROOTS", (str(program),), raising=False
    )
    monkeypatch.setattr(voice_command.os.path, "expanduser", lambda p: str(personal))

    index = voice_command.build_folder_index()
    assert any(e.name == "projetos" for e in index)


def test_resolve_folder_accepts_an_explicit_path(tmp_path):
    _mkdirs(tmp_path, "Projects/alpha")
    result = voice_command.resolve_folder(str(tmp_path / "Projects"), entries=[])
    assert result.ok
    assert result.kind == KIND_FOLDER


def test_resolve_folder_finds_a_shell_folder(folders):
    assert voice_command.resolve_folder("downloads", entries=folders).ok


def test_resolve_folder_ignores_case_and_accents(folders):
    assert voice_command.resolve_folder("DOCUMENTS", entries=folders).ok


def test_resolve_folder_matches_one_word(folders):
    assert voice_command.resolve_folder("videos", entries=folders).ok


def test_resolve_folder_falls_back_to_a_close_spelling(folders):
    assert voice_command.resolve_folder("downlods", entries=folders).ok


def test_resolve_folder_rejects_an_unknown_name(folders):
    result = voice_command.resolve_folder("blablabla", entries=folders)
    assert result.ok is False
    assert result.suggestions == []


def test_resolve_folder_needs_a_name():
    assert voice_command.resolve_folder("   ").ok is False


def test_resolve_folder_strips_quotes(tmp_path):
    _mkdirs(tmp_path, "Music")
    result = voice_command.resolve_folder(f'"{tmp_path / "Music"}"', entries=[])
    assert result.ok


def test_build_folder_index_lists_children(tmp_path):
    _mkdirs(tmp_path, "One", "Two")
    names = {e.name for e in voice_command.build_folder_index([tmp_path])}
    assert names == {"One", "Two"}


# -------------------------------------------------------------------- windows
@pytest.fixture
def wins():
    return [
        WindowInfo(101, "Spotify Premium", 500),
        WindowInfo(102, "Notepad", 501),
        WindowInfo(103, "Documents - File Explorer", 502),
        WindowInfo(104, "Google Chrome - Meeting", 503),
    ]


def test_close_matches_a_window_exactly(wins):
    result = voice_command.resolve_window("notepad", wins=wins)
    assert result.ok
    assert result.kind == KIND_WINDOW
    assert result.handles == (102,)


def test_close_returns_every_match_for_a_multi_window_app(wins):
    result = voice_command.resolve_window("chrome", wins=wins)
    assert result.handles == (104,)
    assert "Meeting" in result.name


def test_close_matches_a_word_inside_the_title(wins):
    result = voice_command.resolve_window("spotify", wins=wins)
    assert result.handles == (101,)


def test_close_finds_an_explorer_window_by_title(wins):
    result = voice_command.resolve_window("Documents - File Explorer", wins=wins)
    assert result.handles == (103,)


def test_close_never_targets_our_own_windows(wins):
    our_own = wins + [WindowInfo(999, "Speech To Text overlay", 4242)]
    result = voice_command.resolve_window("speech to text", wins=our_own, exclude_pid=4242)
    assert result.ok is False


def test_close_reports_an_unknown_title(wins):
    result = voice_command.resolve_window("autocad", wins=wins)
    assert result.ok is False
    assert "No open window" in result.message


# -------------------------------------------------------------- system table
@pytest.mark.parametrize(
    "spoken,canonical",
    [
        ("lock the screen", "lock"),
        ("travar a tela", "lock"),
        ("empty the recycle bin", "empty_recycle_bin"),
        ("esvaziar a lixeira", "empty_recycle_bin"),
        ("volume up", "volume_up"),
        ("aumentar o som", "volume_up"),
        ("mute", "mute"),
        ("take a screenshot", "screenshot"),
        ("show desktop", "show_desktop"),
        ("clear the clipboard", "clear_clipboard"),
        ("sleep", "sleep"),
    ],
)
def test_system_phrases_resolve(spoken, canonical):
    result = voice_command.resolve_system(spoken)
    assert result is not None
    assert result.kind == KIND_SYSTEM
    assert result.target == canonical


def test_an_unknown_phrase_is_not_a_system_action():
    assert voice_command.resolve_system("format the disk") is None


def test_the_whitelist_has_no_destructive_entry():
    joined = " ".join(
        phrase for phrases in voice_command.SYSTEM_ACTIONS.values() for phrase in phrases
    ).lower()
    for banned in ("shutdown", "shut down", "restart", "reboot", "delete", "format", "kill"):
        assert banned not in joined


# --------------------------------------------------------------------- plan
def test_plan_routes_an_app(index, no_path):
    result = voice_command.plan(voice_command.parse_command("open notepad"), index=index)
    assert result.kind == KIND_APP


def test_plan_routes_a_system_action():
    result = voice_command.plan(voice_command.parse_command("lock the screen"))
    assert result.kind == KIND_SYSTEM


def test_plan_skips_system_actions_when_disabled(index, no_path):
    command = voice_command.parse_command("volume up")
    # 'volume up' is not an app, so with the whitelist off nothing resolves.
    result = voice_command.plan(command, index=index, system_enabled=False)
    assert result.ok is False
    assert result.kind == KIND_SYSTEM
    assert "turned off" in result.message


def test_plan_routes_a_url():
    result = voice_command.plan(voice_command.parse_command("open youtube.com"))
    assert result.kind == KIND_URL
    assert result.target == "https://youtube.com"


def test_plan_adds_a_scheme_to_a_www_target():
    result = voice_command.plan(voice_command.parse_command("open www.example.com"))
    assert result.target == "https://www.example.com"


def test_plan_routes_a_folder(tmp_path):
    _mkdirs(tmp_path, "Projects/alpha")
    command = voice_command.parse_command(f'open folder "{tmp_path / "Projects"}"')
    result = voice_command.plan(command)
    assert result.kind == KIND_FOLDER
    assert result.target == str(tmp_path / "Projects")


def test_plan_reports_a_missing_folder(tmp_path):
    command = voice_command.parse_command("open folder blablabla")
    result = voice_command.plan(command, folders=[tmp_path])
    assert result.ok is False
    assert result.kind == KIND_FOLDER


def test_plan_falls_back_to_a_folder_when_no_app_matches(index, no_path, tmp_path):
    """'open Downloads' works without the word 'folder' in it."""
    _mkdirs(tmp_path, "Downloads")
    result = voice_command.plan(
        voice_command.parse_command("open Downloads"), index=index, folders=[
            voice_command.FolderEntry("Downloads", str(tmp_path / "Downloads"))
        ]
    )
    assert result.kind == KIND_FOLDER


def test_plan_prefers_an_app_over_a_same_named_folder(index, no_path, tmp_path):
    _mkdirs(tmp_path, "Notepad")
    result = voice_command.plan(
        voice_command.parse_command("open notepad"), index=index,
        folders=[voice_command.FolderEntry("Notepad", str(tmp_path / "Notepad"))],
    )
    assert result.kind == KIND_APP


def test_plan_routes_a_close_to_a_window(wins):
    result = voice_command.plan(voice_command.parse_command("close notepad"), wins=wins)
    assert result.kind == KIND_WINDOW


def test_plan_asks_for_a_target_when_only_a_verb_was_spoken():
    result = voice_command.plan(voice_command.parse_command("open"))
    assert result.ok is False
    assert "Say what to do" in result.message


# ------------------------------------------------------------------ execute
def test_execute_launches_an_app(index, no_path, monkeypatch):
    started = []
    monkeypatch.setattr(voice_command, "launch", lambda target: started.append(target))
    result = voice_command.execute(
        voice_command.parse_command("open notepad"), index=index
    )
    assert result.ok
    assert started == [r"C:\menu\Notepad.lnk"]


def test_execute_launches_nothing_when_unknown(index, no_path, monkeypatch):
    started = []
    monkeypatch.setattr(voice_command, "launch", lambda target: started.append(target))
    result = voice_command.execute(
        voice_command.parse_command("open autocad"), index=index
    )
    assert result.ok is False
    assert started == []


def test_execute_closes_every_matching_window(wins, monkeypatch):
    closed = []
    monkeypatch.setattr(
        voice_command.windows, "close_windows", lambda handles: closed.extend(handles) or 1
    )
    result = voice_command.execute(
        voice_command.parse_command("close notepad"), wins=wins
    )
    assert result.ok
    assert closed == [102]


def test_execute_runs_a_system_action(monkeypatch):
    done = []
    monkeypatch.setattr(voice_command, "perform_system", lambda key: done.append(key))
    result = voice_command.execute(voice_command.parse_command("lock the screen"))
    assert result.ok
    assert done == ["lock"]


def test_execute_surfaces_a_launch_failure(index, no_path, monkeypatch):
    def boom(target):
        raise OSError("access denied")

    monkeypatch.setattr(voice_command, "launch", boom)
    result = voice_command.execute(
        voice_command.parse_command("open notepad"), index=index
    )
    assert result.ok is False
    assert "access denied" in result.message


def test_execute_surfaces_a_system_failure(monkeypatch):
    def boom(key):
        raise OSError("Windows refused")

    monkeypatch.setattr(voice_command, "perform_system", boom)
    result = voice_command.execute(voice_command.parse_command("lock the screen"))
    assert result.ok is False
    assert "refused" in result.message


def test_execute_text_rejects_dictation():
    result = voice_command.execute_text("the meeting is at noon")
    assert result.ok is False
    assert "Not a command" in result.message


def test_a_near_miss_on_a_system_phrase_is_suggested():
    result = voice_command.execute_text("loking the screen")
    assert result.ok is False
    assert "Locking the screen" in result.suggestions


def test_unrelated_dictation_gets_no_system_suggestions():
    result = voice_command.execute_text("the meeting is at noon")
    assert result.suggestions == []


# ------------------------------------------------------------------- perform
def test_perform_volume_uses_the_media_key(monkeypatch):
    sent = []
    monkeypatch.setattr(voice_command.windows, "broadcast", sent.append)
    voice_command.perform_system("volume_up")
    voice_command.perform_system("mute")
    assert sent == [
        voice_command.windows.APPCOMMAND_VOLUME_UP,
        voice_command.windows.APPCOMMAND_VOLUME_MUTE,
    ]


def test_perform_rejects_an_unknown_action():
    with pytest.raises(ValueError):
        voice_command.perform_system("rm_minus_rf")


def test_launch_uses_the_shell(monkeypatch):
    seen = []
    monkeypatch.setattr(voice_command.os, "startfile", lambda target: seen.append(target))
    voice_command.launch(r"C:\menu\Google Chrome.lnk")
    assert seen == [r"C:\menu\Google Chrome.lnk"]


def test_text_module_is_the_single_normalizer():
    assert text.normalize("Open  Chrome!") == "open chrome"
    assert text.strip_accents("Música") == "Musica"


# ------------------------------------------------------------ index caching
def test_a_fresh_cache_is_not_rebuilt(monkeypatch):
    builds = []
    monkeypatch.setattr(voice_command, "build_index", lambda: builds.append(1) or [])
    voice_command.clear_cache()
    voice_command.cached_index()
    voice_command.cached_index()
    assert len(builds) == 1
    voice_command.clear_cache()


def test_an_expired_cache_is_rebuilt(monkeypatch):
    builds = []
    monkeypatch.setattr(voice_command, "build_index", lambda: builds.append(1) or [])
    voice_command.clear_cache()
    voice_command.cached_index()
    voice_command._index_stamp -= voice_command.INDEX_TTL + 1
    voice_command.cached_index()
    assert len(builds) == 2
    voice_command.clear_cache()


def test_the_refresher_and_a_command_never_build_at_the_same_time(monkeypatch):
    """Both threads ask for the index; only one walk should happen.

    build_index() measured over 2s on a real machine. A voice command landing
    while the refresher was mid-walk used to start a second identical walk,
    doubling that stall.
    """
    active = {"n": 0, "peak": 0}
    gate = threading.Event()

    def slow_build():
        active["n"] += 1
        active["peak"] = max(active["peak"], active["n"])
        gate.wait(2)
        active["n"] -= 1
        return []

    monkeypatch.setattr(voice_command, "build_index", slow_build)
    voice_command.clear_cache()

    worker = threading.Thread(target=voice_command.cached_index)
    worker.start()
    gate.wait(0.2)  # the walk is in flight
    voice_command.cached_index()  # the command path must wait, not duplicate
    gate.set()
    worker.join(5)

    assert active["peak"] == 1
    voice_command.clear_cache()