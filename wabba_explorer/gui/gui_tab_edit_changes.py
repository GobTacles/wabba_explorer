"""Mixin for the 'Edit/Changes' tab and queued InlineFile changes."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .gui_inline_edit import _apply_queued_inline_changes, _ask_wabba_save_as_path


class _TabEditChanges:
    """Builds and drives the 'Edit/Changes' tab in single-file mode."""

    def _init_edit_queue_state(self) -> None:
        self._edit_queue_by_key: dict[str, dict] = {}
        self._edit_queue_order: list[str] = []
        self._edit_changes_listbox = None
        self._edit_changes_detail = None
        self._edit_changes_count_var = None

    def _build_tab_edit_changes(self, tab_label: str = "Edit/Changes") -> None:
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text=tab_label)

        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned)
        right = ttk.Frame(paned)
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        count_var = tk.StringVar(value="Queued changes: 0")
        self._edit_changes_count_var = count_var
        ttk.Label(left, textvariable=count_var, anchor=tk.W).pack(fill=tk.X, padx=6, pady=(6, 2))

        list_frame = ttk.Frame(left)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        listbox = tk.Listbox(list_frame, exportselection=False)
        ysb = ttk.Scrollbar(list_frame, command=listbox.yview)
        listbox.configure(yscrollcommand=ysb.set)
        ysb.pack(side=tk.RIGHT, fill=tk.Y)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        detail = tk.Text(right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        detail.pack(fill=tk.BOTH, expand=True, padx=6, pady=(6, 0))

        buttons = ttk.Frame(right)
        buttons.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(
            buttons,
            text="save/apply to wabba file",
            command=self._apply_queued_changes_inplace,
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            buttons,
            text="save as...",
            command=self._apply_queued_changes_save_as,
        ).pack(side=tk.LEFT)

        self._edit_changes_listbox = listbox
        self._edit_changes_detail = detail

        def _init_sash() -> None:
            total = paned.winfo_width()
            if total > 1:
                # Wider left default than a 50/50 split: ~60/40.
                paned.sashpos(0, int(total * 0.60))

        frame.after(100, _init_sash)

        listbox.bind("<<ListboxSelect>>", lambda _e: self._on_edit_change_selected())

        self._tab_dispatch[tab_label] = {
            "type": "EditChanges",
            "wabba": None,
            "panel": None,
        }
        self._refresh_edit_changes_tab()

    def _queue_inline_change(self, change: dict) -> None:
        """Upsert a queued InlineFile change (latest wins per target key)."""
        key = str(change.get("queue_key", "") or "")
        if not key:
            return
        if key not in self._edit_queue_by_key:
            self._edit_queue_order.append(key)
        self._edit_queue_by_key[key] = change
        self._refresh_edit_changes_tab()

    def _clear_queued_changes(self) -> None:
        self._edit_queue_by_key.clear()
        self._edit_queue_order.clear()
        self._refresh_edit_changes_tab()

    def _get_queued_changes(self) -> list[dict]:
        return [
            self._edit_queue_by_key[k]
            for k in self._edit_queue_order
            if k in self._edit_queue_by_key
        ]

    def _refresh_edit_changes_tab(self) -> None:
        listbox = self._edit_changes_listbox
        if listbox is None:
            return

        changes = self._get_queued_changes()
        selected_idx = listbox.curselection()[0] if listbox.curselection() else 0

        listbox.delete(0, tk.END)
        if not changes:
            listbox.insert(tk.END, "(no queued changes)")
            listbox.selection_set(0)
        else:
            for item in changes:
                d = item.get("display", {})
                summary = str(d.get("summary", "") or "")
                if not summary:
                    op = str(item.get("op", "") or "edit")
                    to_path = str(item.get("to_path", "") or "")
                    summary = f"{op}: {to_path}"
                listbox.insert(tk.END, summary)
            idx = max(0, min(selected_idx, len(changes) - 1))
            listbox.selection_set(idx)

        if self._edit_changes_count_var is not None:
            self._edit_changes_count_var.set(f"Queued changes: {len(changes)}")

        self._on_edit_change_selected()

    def _on_edit_change_selected(self) -> None:
        detail = self._edit_changes_detail
        listbox = self._edit_changes_listbox
        if detail is None or listbox is None:
            return

        idxs = listbox.curselection()
        text = ""
        changes = self._get_queued_changes()
        if not changes:
            text = "No queued changes.\n\nReplace an InlineFile and choose 'queue changes for later'."
        elif not idxs:
            text = "Select a queued change to view details."
        else:
            idx = idxs[0]
            if 0 <= idx < len(changes):
                item = changes[idx]
                d = item.get("display", {})
                lines = [
                    "Queued change:",
                    "",
                    str(d.get("summary", "") or str(item.get("op", ""))),
                ]
                for detail_line in d.get("details", []) or []:
                    lines.append(str(detail_line))
                lines.extend(
                    [
                        "",
                        f"Operation: {item.get('op', '')}",
                        f"To: {item.get('to_path', '')}",
                        f"SourceDataID: {item.get('source_id', '')}",
                        f"Source file: {item.get('replacement_path', '')}",
                    ]
                )
                long_text = str(d.get("long_text", "") or "")
                if long_text:
                    lines.extend(
                        [
                            "",
                            "Affected items:",
                            long_text,
                        ]
                    )
                text = "\n".join(lines)

        detail.configure(state=tk.NORMAL)
        detail.delete("1.0", tk.END)
        detail.insert(tk.END, text)
        detail.configure(state=tk.DISABLED)

    def _apply_queued_changes_inplace(self) -> bool:
        wabba = getattr(self, "_wabba", None)
        changes = self._get_queued_changes()
        if wabba is None:
            return False
        ok = _apply_queued_inline_changes(wabba, changes)
        if ok:
            path = wabba.path
            self._clear_queued_changes()
            self._load_file(path)
        return ok

    def _apply_queued_changes_save_as(self) -> bool:
        wabba = getattr(self, "_wabba", None)
        changes = self._get_queued_changes()
        if wabba is None:
            return False

        save_path = _ask_wabba_save_as_path(wabba.path)
        if not save_path:
            return False

        return _apply_queued_inline_changes(
            wabba,
            changes,
            save_as_path=save_path,
        )
