"""Mixin for the 'Files' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .fs_tree_panel import _FsTreePanel


class _TabFiles:
    """Builds and drives the 'Files' tab."""

    def _build_tab_file_explorer(self, tab_label: str = "Files", wabba=None) -> None:
        """Virtual filesystem tree built from Directive 'To' paths.

        *wabba* is the WabbaFile this tab is bound to.  ``None`` means
        single-file mode (uses ``self._wabba`` dynamically).
        """
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text=tab_label)

        files_panel = _FsTreePanel(frame)
        files_panel.pack(fill=tk.BOTH, expand=True)

        if wabba is None:
            self._files_panel = files_panel

        self._tab_dispatch[tab_label] = {
            "type": "Files",
            "wabba": wabba,
            "panel": files_panel,
        }
