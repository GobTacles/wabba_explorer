# wabba_explorer

Open and inspect [Wabbajack](https://www.wabbajack.org/) `.wabbajack` archive files.

A `.wabbajack` file is a ZIP archive.  This tool keeps the file handle open
(no full extraction) so it works with the large archives typical of modlists.

---

## Features

* List files in the archive root.
* Read and display the `modlist` metadata entry (JSON).
* Modular loader (`WabbaFile`) designed to support two simultaneous open
  archives for future side-by-side comparison.
* **GUI** (tkinter – cross-platform) and **CLI** modes. (cli not yet functional)

---

## Quick start

### Windows (standalone exe – no Python needed)

Download the release ZIP, unpack it, and double-click **`wabba_explorer.exe`**.

The exe opens the GUI directly.  You can also use it from the command line:

```
wabba_explorer.exe path\to\file.wabbajack
wabba_explorer.exe --cli path\to\file.wabbajack
```

---

### Linux / macOS (system Python 3.10+)

```bash
# clone or unpack the source, then:
python main.py                                   # open GUI
python main.py path/to/file.wabbajack            # CLI
python main.py --cli path/to/file.wabbajack      # CLI (explicit)
python main.py --gui path/to/file.wabbajack      # GUI, open file immediately
```

## Building the Windows executable yourself

Prerequisites (one-time):

```
pip install pyinstaller
```

Then, from the repository root:

```
build_windows.bat          # Windows batch script
# or
python build.py            # cross-platform Python helper
```

Output: `dist\wabba_explorer.exe`

---

## windows python in vscode + terminal 

- in vscode terminal, setup venv and activate with .venv\Scripts\activate.bat
- pip install xxhash
- vscode task (shift+F5)

## Project structure

```
wabba_explorer/
├── main.py                  # entry point – dispatches to CLI or GUI
├── wabba_explorer/
│   ├── __init__.py
│   ├── wabba_file.py        # WabbaFile – modular ZIP loader
│   ├── cli.py               # CLI mode
│   └── gui.py               # GUI mode (tkinter)
├── requirements.txt         # PyInstaller (only needed for building)
├── build_windows.bat        # Windows build script
└── build.py                 # cross-platform build helper
```
