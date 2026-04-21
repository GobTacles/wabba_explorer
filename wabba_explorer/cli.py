"""CLI mode for wabba_explorer.

Usage examples
--------------
  python main.py path/to/file.wabbajack
  python main.py --cli path/to/file.wabbajack
"""

import argparse
import json
import sys
import zipfile

from .wabba_file import WabbaFile
from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wabba_explorer",
        description="Inspect a .wabbajack archive.",
    )
    parser.add_argument("file", help="Path to the .wabbajack file.")
    parser.add_argument(
        "--no-modlist",
        action="store_true",
        help="Skip reading the 'modlist' entry.",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    """Entry point for CLI mode.  *argv* defaults to sys.argv[1:] minus
    any ``--cli`` flag already consumed by main.py."""
    print(f"wabba_explorer {__version__}")
    parser = build_parser()
    args = parser.parse_args(argv)

    wabba = WabbaFile(args.file)
    try:
        wabba.open()
    except FileNotFoundError:
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)
    except zipfile.BadZipFile as exc:
        print(f"Error: not a valid ZIP/wabbajack file: {exc}", file=sys.stderr)
        sys.exit(1)

    with wabba:
        # --- list root files -------------------------------------------
        root_files = wabba.list_root_files()
        print(f"Root entries in '{args.file}':")
        for name in root_files:
            print(f"  {name}")

        if not root_files:
            print("  (none)")

        # --- modlist ---------------------------------------------------
        if not args.no_modlist:
            print()
            try:
                raw = wabba.read_modlist()
                print("=== modlist ===")
                try:
                    obj = json.loads(raw)
                    print(json.dumps(obj, indent=2))
                except json.JSONDecodeError:
                    # not JSON – print as text
                    print(raw.decode("utf-8", errors="replace"))
            except FileNotFoundError as exc:
                print(f"Warning: {exc}", file=sys.stderr)
