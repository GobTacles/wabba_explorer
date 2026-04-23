"""Mixin for the 'modlist json' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .gui_util import _WABBA_FILE_KEY, _key_label, _preview_value


class _TabModlistJson:
    """Builds and drives the 'modlist json' tab."""

    def _build_tab_modlist_json(self, tab_label: str = "main", wabba=None) -> None:
        """Tab: key list on the left, text preview on the right.

        *wabba* is the WabbaFile this tab is bound to.  ``None`` means
        single-file mode (uses ``self._wabba`` dynamically).
        """
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text=tab_label)

        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, padding=4)
        paned.add(left, weight=1)

        key_listbox = tk.Listbox(left, activestyle="dotbox")
        left_scroll = ttk.Scrollbar(left, command=key_listbox.yview)
        key_listbox.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        key_listbox.pack(fill=tk.BOTH, expand=True)

        right = ttk.Frame(paned, padding=4)
        paned.add(right, weight=3)
        content_label = ttk.Label(right, text="Preview", anchor=tk.W)
        content_label.pack(fill=tk.X, pady=(0, 4))

        content_text = tk.Text(
            right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        right_scroll = ttk.Scrollbar(right, command=content_text.yview)
        content_text.configure(yscrollcommand=right_scroll.set)
        right_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        content_text.pack(fill=tk.BOTH, expand=True)

        # Mutable state shared between closures for this specific tab.
        tab_state: dict = {
            "modlist_data": None,
            "modlist_keys": [],
            "wabba_file_preview": "",
        }

        def _set_content(text: str) -> None:
            content_text.configure(state=tk.NORMAL)
            content_text.delete("1.0", tk.END)
            content_text.insert(tk.END, text)
            content_text.configure(state=tk.DISABLED)

        def _on_key_select(_event=None) -> None:
            if tab_state["modlist_data"] is None:
                return
            selection = key_listbox.curselection()
            if not selection:
                return
            idx = selection[0]
            keys = tab_state["modlist_keys"]
            if idx >= len(keys):
                return
            key = keys[idx]
            content_label.configure(text=f"modlist → {key}")
            if key == _WABBA_FILE_KEY:
                text = tab_state["wabba_file_preview"]
            else:
                text = _preview_value(key, tab_state["modlist_data"][key])
            _set_content(text)

        key_listbox.bind("<<ListboxSelect>>", _on_key_select)

        # Keep self.* pointing at the single-file tab for backward compat.
        if wabba is None:
            self._key_listbox = key_listbox
            self._content_label = content_label
            self._content_text = content_text

        self._tab_dispatch[tab_label] = {
            "type": "Main",
            "wabba": wabba,
            "key_listbox": key_listbox,
            "content_label": content_label,
            "tab_state": tab_state,
            "set_content": _set_content,
        }
