"""Mixin for the 'Problems' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .problems_panel import _ProblemsPanel


class _TabProblems:
    """Builds and drives the 'Problems' tab."""

    def _build_tab_problems(self) -> None:
        """Directive hash mismatch analysis with progress."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="Problems")

        self._problems_panel = _ProblemsPanel(frame)
        self._problems_panel.pack(fill=tk.BOTH, expand=True)
