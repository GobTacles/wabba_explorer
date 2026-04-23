"""Menu bar and recent-files helpers for WabbaExplorerApp."""

from __future__ import annotations

import json
import pathlib
import tkinter as tk

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
            data = json.loads(_RECENT_FILES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._recent_files = [p for p in data if isinstance(p, str)]
        except (OSError, json.JSONDecodeError):
            self._recent_files = []

    def _save_recent_files(self) -> None:
        try:
            _RECENT_FILES_PATH.write_text(
                json.dumps(self._recent_files, indent=2), encoding="utf-8"
            )
        except OSError:
            pass
