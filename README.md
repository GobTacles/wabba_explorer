# wabba_explorer

Open and inspect [Wabbajack](https://www.wabbajack.org/) `.wabbajack` archive files for game modding

- A `.wabbajack` file is a ZIP archive with a big modlist json file and a lot of uuid inline files used by it. 
- The Directives install the modlist by asking the user to download mods from nexus and other places, and apply patches and custom configs etc. 
- A .wabbajack file can be 2GiB in size and have over 300k Directives, and refer to over 1k mod downloads.

---

## Features

* inspect wabba files and do some basic/limited editing
* list+filter archives(downloads folder), directives(wabba install), file tree(from paths) from wabbajack modlist files
* compare two wabba files, list updated/removed/added archives(downloads folder)
* extract inline files
* problem check for hash mismatch, unused/missing files, large files where only a small fraction is used...
* extract and/or generate .meta files for download folder
* **GUI** (tkinter – cross-platform), windows exe or direct python (pip install xxhash)
* github workflow to compile/build/release in case someone wants to fork it
* Edit/Changes tab to queue and review changes before write/apply/save
* edit : add/remove/replace InlineFiles
* edit : replace a FromArchive file by a new inlined file (e.g. overwrite config .ini)
* edit : remove an archive(mod download) and all directives using it

## feature ideas

- export or generate all the metas from the archives to a folder (to set up a compile environment)
- generate changelog draft, at least for the compare old->new updated/added/removed entries in compare mode: archives tab
- upgrading/downgrading a "simple" mod, one directive per sub-file, but should be doable if it doesnt involve binary patching. "simple" as in it doesnt involve fomod wizard or other complex mo2 functions
- "update" a archive entry (mod/download). like use a newer version of a mod without having do a full wabba recompile. will only work if theres no fancy inline patching of changed files, but from what i've seen i think it would actually work in a lot of cases. that would make hotfixing so much easier. sure it still has to be tested if new features break things etc, but an interesting option compared to setting up a clean environment and doing a full wabba recompile
- shared downloads folder : move used archives to new folder, to set up a wabba compile environment (if you have a big shared download folder and a working wabba file and you want to set things up so you can compile wabba without downloading everything from scratch, then i could make an option to copy only the needed downloads to a new download folder based on the "Archives" json data and also check if hashes match. i think i could also generate the .meta files in the downloads folder from the "Archives" json data)
- (partially done) : compare mode for directives/inline files (and at least detect + list that binary patch is different hash-wise, even if we cant parse the octodelta/patch format yet)
- scan a modlist installation folder for files that have been modified. shouldnt be too hard for config files that are inlined directly. i could also list files added by the user or generated while playing. warnings for binary patched files that i cant analyze YET (until i figure out the OCTODELTA format i've seen in patch directives) , wont work for BSA.
- modlist installation folder scan : "basic" revert to default option for changed files/configs, should be doable if they are directly from inlinefile or archive(mod, user needs to have that file on hand), if it doesnt involve binary patching/octodelta

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
├── wabba_explorer/          # python scripts
├── requirements.txt         # PyInstaller (only needed for building)
├── build_windows.bat        # Windows build script
└── build.py                 # cross-platform build helper
```
