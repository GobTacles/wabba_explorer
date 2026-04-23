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

## feature ideas

- edit/update inline file  (like tweak a config/.ini ,  if its just used directly instead of complex binary patching that should be doable)
- shared downloads folder : move used archives to new folder, to set up a wabba compile environment  (if you have a big shared download folder and a working wabba file and you want to set things up so  you can compile wabba without downloading everything from scratch, then i could make an option to copy only the needed downloads to a new download folder based on the "Archives" json data and also check if hashes matchs. i think i could also generate the .meta files in the downloads folder from the "Archives" json data)
- compare two wabba files (like different versions) to see what actually changed
- "update" a archive entry (mod/download).   like use a newer version of a mod without having do a full wabba recompile.  will only work if theres no fancy inline patching of changed files, but from what i've seen i think it would actually work in a lot of cases.  that would make hotfixing so much easier. sure it still has to be tested if new features break things etc, but still.
- scan a modlist installation folder for files that have been modified (shouldnt be too hard for config files that are inlined directly.  i could also list "added" files.   warnings for binary patched files that i cant analyze YET (until i figure out the OCTODELTA format i've seen in patch directives) , wont work for BSA.
- maybe even "revert to default" option for directly inlined files like configs

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
pip install -r requirements.txt
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
