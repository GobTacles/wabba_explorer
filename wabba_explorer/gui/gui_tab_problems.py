"""Mixin for the 'Problems' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .problems_panel import _ProblemsPanel


class _TabProblems:
    """Builds and drives the 'Problems' tab."""

    def _build_tab_problems(self, tab_label: str = "Problems", wabba=None) -> None:
        """Directive hash mismatch analysis with progress.

        *wabba* is the WabbaFile this tab is bound to.  ``None`` means
        single-file mode (uses ``self._wabba`` dynamically).
        """
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text=tab_label)

        problems_panel = _ProblemsPanel(frame)
        problems_panel.pack(fill=tk.BOTH, expand=True)

        if wabba is None:
            self._problems_panel = problems_panel

        self._tab_dispatch[tab_label] = {
            "type": "Problems",
            "wabba": wabba,
            "panel": problems_panel,
        }
