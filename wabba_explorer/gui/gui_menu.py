"""Menu bar and recent-files helpers for WabbaExplorerApp."""

from __future__ import annotations

import json
import pathlib
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

_RECENT_FILES_PATH = pathlib.Path.home() / ".wabba_explorer_recent"


class _MenuMixin:
    """Mixin that provides menu bar construction and recent-files management."""

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menubar(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Open File…", command=self._on_open)
        file_menu.add_command(label="Compare Files...", command=self._on_compare_files)
        self._recent_files_menu = tk.Menu(file_menu, tearoff=0)
        file_menu.add_cascade(label="Recent Files", menu=self._recent_files_menu)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.quit)
        menubar.add_cascade(label="File", menu=file_menu)
        self._refresh_recent_files_menu()

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About / Licenses…", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menubar)

    def _refresh_recent_files_menu(self) -> None:
        if self._recent_files_menu is None:
            return
        self._recent_files_menu.delete(0, tk.END)
        if not self._recent_files:
            self._recent_files_menu.add_command(label="(none)", state=tk.DISABLED)
            return
        for path in self._recent_files:
            self._recent_files_menu.add_command(
                label=path,
                command=lambda p=path: self._load_file(p),
            )

    def _remember_recent_file(self, path: str) -> None:
        self._recent_files = [p for p in self._recent_files if p != path]
        self._recent_files.insert(0, path)
        self._recent_files = self._recent_files[: self._max_recent_files]
        self._refresh_recent_files_menu()
        self._save_recent_files()

    def _load_recent_files(self) -> None:
        try:
            raw = json.loads(_RECENT_FILES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = None

        if isinstance(raw, list):
            # Legacy format: bare list of paths.
            self._recent_files = [p for p in raw if isinstance(p, str)]
        elif isinstance(raw, dict):
            files = raw.get("recent_files", [])
            self._recent_files = [p for p in files if isinstance(p, str)]
            self._last_compare_a = raw.get("last_compare_a", "")
            self._last_compare_b = raw.get("last_compare_b", "")
            self._last_mode = raw.get("last_mode", "normal")
        else:
            self._recent_files = []

    def _save_recent_files(self) -> None:
        try:
            _RECENT_FILES_PATH.write_text(
                json.dumps(
                    {
                        "recent_files": self._recent_files,
                        "last_compare_a": getattr(self, "_last_compare_a", ""),
                        "last_compare_b": getattr(self, "_last_compare_b", ""),
                        "last_mode": getattr(self, "_last_mode", "normal"),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _remember_compare_files(self, path_a: str, path_b: str) -> None:
        """Persist the two compare-mode paths and set last_mode to 'compare'."""
        self._last_compare_a = path_a
        self._last_compare_b = path_b
        self._last_mode = "compare"
        self._save_recent_files()

    def _remember_normal_mode(self) -> None:
        """Persist last_mode as 'normal'."""
        self._last_mode = "normal"
        self._save_recent_files()

    def _on_compare_files(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("Compare files")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)

        path_a = tk.StringVar(value=getattr(self, "_last_compare_a", ""))
        path_b = tk.StringVar(value=getattr(self, "_last_compare_b", ""))

        recent = getattr(self, "_recent_files", [])

        def _browse(target: tk.StringVar) -> None:
            chosen = filedialog.askopenfilename(
                title="Open .wabbajack file",
                filetypes=[("Wabbajack archives", "*.wabbajack"), ("All files", "*.*")],
            )
            if chosen:
                target.set(chosen)

        for row, label, var in (
            (0, "A/old", path_a),
            (1, "B/new", path_b),
        ):
            ttk.Label(frame, text=f"{label}:").grid(row=row, column=0, sticky="w", padx=(0, 6), pady=4)
            combo = ttk.Combobox(frame, textvariable=var, values=recent, width=136)
            combo.grid(row=row, column=1, sticky="ew", pady=4)
            ttk.Button(frame, text="Browse…", command=lambda v=var: _browse(v)).grid(
                row=row, column=2, padx=(6, 0), pady=4
            )

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=3, sticky="e", pady=(10, 0))

        def _do_compare() -> None:
            a = path_a.get().strip()
            b = path_b.get().strip()
            if not a or not b:
                messagebox.showerror("Compare files", "Please select both files.")
                return
            dialog.destroy()
            # _rebuild_main_tabs opens both files and starts both pipelines.
            self._rebuild_main_tabs(compare_mode=True, compare_paths={"A": a, "B": b})

        ttk.Button(buttons, text="cancel", command=dialog.destroy).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(buttons, text="compare", command=_do_compare).pack(side=tk.LEFT)
        frame.columnconfigure(1, weight=1)
