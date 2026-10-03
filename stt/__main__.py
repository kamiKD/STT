"""CLI entry point: `python -m stt`."""

from __future__ import annotations

import argparse
import sys

from . import config as config_mod


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="stt", description="Hold-to-talk dictation into the focused window."
    )
    parser.add_argument("--hotkey", help='override hotkey, e.g. "ctrl+shift+m"')
    parser.add_argument(
        "--command-hotkey", help='command hotkey, e.g. "ctrl+shift+o"'
    )
    parser.add_argument("--language", help="language code, or 'auto'")
    parser.add_argument("--model", choices=config_mod.MODELS)
    parser.add_argument(
        "--overlay-position", choices=config_mod.OVERLAY_POSITIONS,
        help="where the pill appears: bottom or top center",
    )
    parser.add_argument("--no-tray", action="store_true", help="run without a tray icon")
    parser.add_argument(
        "--check", action="store_true", help="print diagnostics and exit"
    )
    args = parser.parse_args(argv)

    cfg = config_mod.load_config()
    if args.hotkey or args.command_hotkey:
        from . import keys as keyutil

        for flag, name in (
            ("--hotkey", "hotkey"),
            ("--command-hotkey", "command_hotkey"),
        ):
            value = getattr(args, flag.lstrip("-").replace("-", "_"))
            if not value:
                continue
            try:
                cfg[name] = keyutil.parse_combo(value)
            except ValueError as exc:
                print(f"invalid {flag}: {exc}", file=sys.stderr)
                return 2
        if cfg["hotkey"] == cfg["command_hotkey"]:
            print(
                "the dictation and command hotkeys must differ",
                file=sys.stderr,
            )
            return 2
    if args.language:
        if args.language not in config_mod.LANGUAGES:
            print(
                f"unknown language '{args.language}'. valid: "
                + ", ".join(config_mod.LANGUAGES),
                file=sys.stderr,
            )
            return 2
        cfg["language"] = args.language
    if args.model:
        cfg["model"] = args.model
    if args.overlay_position:
        cfg["overlay_position"] = args.overlay_position
    cfg.save()

    if args.check:
        return _check(cfg)

    from .app import run  # imported late so --check works without a GUI stack

    return run(cfg)


def _check(cfg) -> int:
    from . import keys as keyutil
    from .audio import Recorder

    print(f"hotkey       : {keyutil.combo_label(cfg['hotkey'])}")
    print(f"cmd hotkey   : {keyutil.combo_label(cfg['command_hotkey'])}")
    print(f"voice cmd    : {'on' if cfg['command_with_voice'] else 'off'}")
    print(f"sys commands : {'on' if cfg['command_system_enabled'] else 'off'}")
    print(f"model        : {cfg['model']}")
    print(f"language     : {cfg['language']}")
    print(f"api key      : {config_mod.mask_key(config_mod.read_api_key())}")
    print(f"config       : {config_mod.config_path()}")
    try:
        inputs = Recorder(sample_rate=cfg["sample_rate"]).list_devices()
        if inputs:
            for dev in inputs:
                mark = "*" if dev["index"] == Recorder().default_input() else " "
                print(f"input {mark}     : {dev['name']}")
        else:
            print("input       : none found")
    except Exception as exc:
        print(f"input         : error {exc}")
    try:
        from . import voice_command

        print(f"apps         : {len(voice_command.cached_index())} launchable entries")
        print(f"folders      : {len(voice_command.cached_folders())} searchable directories")
        print(f"sys actions  : {len(voice_command.SYSTEM_ACTIONS)} whitelisted")
    except Exception as exc:  # pragma: no cover - filesystem dependent
        print(f"voice index  : error {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
