"""Mixin for the 'modlist json' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .gui_util import _key_label, _preview_value


class _TabModlistJson:
    """Builds and drives the 'modlist json' tab."""

    def _build_tab_modlist_json(self) -> None:
        """Tab 1: key list on the left, text preview on the right."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="modlist json")

        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.LabelFrame(paned, text="modlist keys", padding=4)
        paned.add(left, weight=1)

        self._key_listbox = tk.Listbox(left, activestyle="dotbox")
        left_scroll = ttk.Scrollbar(left, command=self._key_listbox.yview)
        self._key_listbox.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._key_listbox.pack(fill=tk.BOTH, expand=True)
        self._key_listbox.bind("<<ListboxSelect>>", self._on_key_select)

        right = ttk.LabelFrame(paned, text="Preview", padding=4)
        paned.add(right, weight=3)
        self._content_label = right

        self._content_text = tk.Text(
            right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        right_scroll = ttk.Scrollbar(right, command=self._content_text.yview)
        self._content_text.configure(yscrollcommand=right_scroll.set)
        right_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._content_text.pack(fill=tk.BOTH, expand=True)

    def _on_key_select(self, _event=None) -> None:
        if self._modlist_data is None:
            return
        selection = self._key_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx >= len(self._modlist_keys):
            return
        key = self._modlist_keys[idx]
        self._content_label.configure(text=f"modlist → {key}")
        text = _preview_value(key, self._modlist_data[key])
        self._set_content(text)

    def _set_content(self, text: str) -> None:
        self._content_text.configure(state=tk.NORMAL)
        self._content_text.delete("1.0", tk.END)
        self._content_text.insert(tk.END, text)
        self._content_text.configure(state=tk.DISABLED)
