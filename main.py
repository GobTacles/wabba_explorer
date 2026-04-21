"""Entry point for wabba_explorer.

Modes
-----
GUI (default when no file argument is given):
  python main.py
  python main.py --gui
  python main.py --gui path/to/file.wabbajack

CLI (prints info to stdout):
  python main.py path/to/file.wabbajack
  python main.py --cli path/to/file.wabbajack
  python main.py --cli path/to/file.wabbajack --no-modlist
"""

import argparse
import sys


def _build_top_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wabba_explorer",
        description="Open and inspect .wabbajack archive files.",
        add_help=False,  # we'll add help manually after we know the mode
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run in command-line mode (print to stdout).",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Run in graphical mode (default when no file is given).",
    )
    parser.add_argument(
        "-h", "--help",
        action="store_true",
        help="Show this help message and exit.",
    )
    return parser


def main() -> None:
    top_parser = _build_top_parser()
    top_args, remaining = top_parser.parse_known_args()

    # Decide mode: explicit --cli overrides; --gui forces GUI even with a file;
    # if a positional file is present and no --gui, default to CLI.
    force_gui = top_args.gui
    force_cli = top_args.cli

    if top_args.help and not force_cli and not force_gui:
        top_parser.print_help()
        print("\n  Pass --cli or --gui to get mode-specific help.")
        sys.exit(0)

    # Peek at remaining to detect a positional file arg.
    has_file_arg = bool(remaining) and not remaining[0].startswith("-")

    use_cli = force_cli or (has_file_arg and not force_gui)

    if use_cli:
        from wabba_explorer.cli import run_cli
        # Pass remaining args (plus help flag re-injected if needed) to CLI
        cli_argv = remaining
        if top_args.help:
            cli_argv = ["--help"] + cli_argv
        run_cli(cli_argv)
    else:
        from wabba_explorer.gui import run_gui
        # If a file was given alongside --gui, open it directly.
        initial_file = remaining[0] if remaining and not remaining[0].startswith("-") else None
        run_gui(initial_file)


if __name__ == "__main__":
    main()
