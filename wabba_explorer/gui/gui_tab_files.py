"""Mixin for the 'Files' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .fs_tree_panel import _FsTreePanel


class _TabFiles:
    """Builds and drives the 'Files' tab."""

    def _build_tab_file_explorer(self) -> None:
        """Virtual filesystem tree built from Directive 'To' paths."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="Files")

        self._files_panel = _FsTreePanel(frame)
        self._files_panel.pack(fill=tk.BOTH, expand=True)
