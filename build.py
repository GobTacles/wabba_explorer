"""Cross-platform build helper (alternative to build_windows.bat).

Run with:
  python build.py

Produces a standalone executable via PyInstaller.
On Windows:  dist/wabba_explorer.exe
On Linux:    dist/wabba_explorer
"""

import subprocess
import sys


def main() -> None:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name", "wabba_explorer",
        "main.py",
    ]
    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print("\nBuild succeeded.  Executable is in dist/")
    else:
        print("\nBuild failed.", file=sys.stderr)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
