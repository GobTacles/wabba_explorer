"""GUI mode for wabba_explorer (tkinter – cross-platform, bundled with Python).

Layout
------
  ┌─────────────────────────────────────────────────────────────┐
  │  Menu: File (Open, Recent) / Help                           │
  ├─────────────────────────────────────────────────────────────┤
  │  [modlist json]  [Files]  [Archives]  [Directives]  [Problems] │
  │  ┌─────────────────────────────────────────────────────────┐│
  │  │  tab content (key list or filtered list | text preview) ││
  │  └─────────────────────────────────────────────────────────┘│
  ├─────────────────────────────────────────────────────────────┤
  │  [Console]  (readonly stdout – selectable/copyable)         │
  └─────────────────────────────────────────────────────────────┘
  │  Status bar                                                  │
  └─────────────────────────────────────────────────────────────┘
"""

import io
import base64
import json
import os
import pathlib
import re
import sys
import threading
import time
import tkinter as tk
from collections import Counter
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from .WabbaHash import WabbaHashXX64, WabbaHashXX64_stream
from . import __version__
from .wabba_file import WabbaFile

_PREVIEW_MAX_CHARS = 4096
_PREVIEW_HEAD = 5   # first N items shown in modlist-json tab
_PREVIEW_TAIL = 5   # last  N items shown in modlist-json tab
_FILTER_DEBOUNCE_MS = 300
_INLINE_PREVIEW_MAX = 256 * 1024  # 256 KiB – unpack InlineFile entries below this size
_INLINE_WABBAHASH_MAX = 128 * 1024 * 1024  # 128 MiB
_PROBLEMS_UPDATE_INTERVAL_SECS = 2.0
_PROBLEMS_IID = "__PROBLEMS__"
_RECENT_FILES_PATH = pathlib.Path.home() / ".wabba_explorer_recent"


# ---------------------------------------------------------------------------
# Stdout redirect
# ---------------------------------------------------------------------------

class _StdoutRedirect(io.TextIOBase):
    """Tee stdout to a tkinter Text widget (readonly) and the real stdout.

    Thread-safe: calls from non-main threads are marshalled to the main
    thread via ``widget.after(0, ...)`` so Tkinter is never touched from
    a background thread.
    """

    def __init__(self, text_widget: tk.Text, original) -> None:
        self._widget = text_widget
        self._original = original
        self._main_thread_id = threading.main_thread().ident

    def write(self, s: str) -> int:
        if threading.current_thread().ident == self._main_thread_id:
            self._write_to_widget(s)
        else:
            self._widget.after(0, self._write_to_widget, s)
        if self._original is not None and hasattr(self._original, "write"):
            self._original.write(s)
        return len(s)

    def _write_to_widget(self, s: str) -> None:
        self._widget.configure(state=tk.NORMAL)
        self._widget.insert(tk.END, s)
        self._widget.see(tk.END)
        self._widget.configure(state=tk.DISABLED)

    def flush(self) -> None:
        if self._original is not None and hasattr(self._original, "flush"):
            self._original.flush()


# ---------------------------------------------------------------------------
# Tooltip helper
# ---------------------------------------------------------------------------

class _Tooltip:
    """Simple hover tooltip for any tkinter widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, event=None) -> None:
        if self._tip is not None:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 2
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self._tip,
            text=self._text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            font=("TkDefaultFont", 8),
        )
        lbl.pack(ipadx=4, ipady=2)

    def _hide(self, event=None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


# ---------------------------------------------------------------------------
# Reusable filtered-list + preview panel
# ---------------------------------------------------------------------------

class _FilteredListPanel(ttk.Frame):
    """Horizontal paned widget: (filtered listbox + filter entry) | text preview.

    *label_fn*  – callable(item) -> str   label shown in the listbox
    *filter_fn* – callable(item, text) -> bool   True if item matches filter
                  (defaults to checking whether *text* appears in the label)
    """

    def __init__(
        self,
        parent,
        label_fn: Callable,
        filter_fn: Callable | None = None,
        extra_info_fn: Callable | None = None,
        extra_controls_fn: Callable | None = None,
        item_filter_fn: Callable | None = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self._label_fn = label_fn
        self._filter_fn = filter_fn or (lambda item, t, pat: pat.search(label_fn(item)) is not None)
        self._extra_info_fn = extra_info_fn
        self._extra_controls_fn = extra_controls_fn  # callable(left_frame) -> None
        self._item_filter_fn = item_filter_fn          # callable(item) -> bool, extra gate
        self._all_items: list = []
        self._filtered_indices: list[int] = []
        self._filter_job: str | None = None
        self._build()

    def _build(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- left side: listbox + filter bar ---
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        self._list_var = tk.StringVar()
        self._listbox = tk.Listbox(
            left, listvariable=self._list_var, activestyle="dotbox"
        )
        sb = ttk.Scrollbar(left, command=self._listbox.yview)
        self._listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(fill=tk.BOTH, expand=True)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        filter_bar = ttk.Frame(left)
        filter_bar.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(filter_bar, text="Filter:").pack(side=tk.LEFT)
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", self._on_filter_change)
        _PLACEHOLDER = "^=start, *=wildcard"
        # Use a plain tk.Entry (not ttk) so we can set foreground for the placeholder
        self._filter_count_var = tk.StringVar(value="")
        ttk.Label(filter_bar, textvariable=self._filter_count_var).pack(side=tk.RIGHT, padx=(4, 0))
        self._filter_entry = tk.Entry(filter_bar, foreground="gray")
        self._filter_entry.insert(0, _PLACEHOLDER)
        self._filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _Tooltip(
            self._filter_entry,
            "^=anchor to start, *=any characters\nExample: ^Begin*Middle",
        )

        # Use a mutable flag so nested functions share one consistent state.
        _ph_active = [True]  # True while the placeholder is showing

        def _on_focus_in(event, _entry=self._filter_entry, _state=_ph_active) -> None:
            if _state[0]:
                _state[0] = False
                _entry.configure(foreground="black")
                _entry.delete(0, tk.END)

        def _on_focus_out(
            event,
            _entry=self._filter_entry,
            _var=self._filter_var,
            _state=_ph_active,
            _ph=_PLACEHOLDER,
        ) -> None:
            if not _entry.get():
                _state[0] = True
                _var.set("")
                _entry.configure(foreground="gray")
                _entry.delete(0, tk.END)
                _entry.insert(0, _ph)

        def _on_key_release(
            event,
            _entry=self._filter_entry,
            _var=self._filter_var,
            _state=_ph_active,
        ) -> None:
            if not _state[0]:
                new_val = _entry.get()
                if _var.get() != new_val:
                    _var.set(new_val)

        self._filter_entry.bind("<FocusIn>", _on_focus_in)
        self._filter_entry.bind("<FocusOut>", _on_focus_out)
        self._filter_entry.bind("<KeyRelease>", _on_key_release)

        if self._extra_controls_fn is not None:
            self._extra_controls_fn(left)

        # --- right side: preview text ---
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        self._preview = tk.Text(
            right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        sb2 = ttk.Scrollbar(right, command=self._preview.yview)
        self._preview.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self._preview.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------

    def set_loading(self) -> None:
        """Show a 'Loading…' placeholder in both the listbox and the preview pane."""
        self._all_items = []
        self._filtered_indices = []
        self._list_var.set(["Loading…"])
        self._listbox.yview_moveto(0)
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert(tk.END, "Loading…")
        self._preview.configure(state=tk.DISABLED)

    def load_items(self, items: list) -> None:
        self._all_items = items
        self._apply_filter(self._filter_var.get())
        # Ensure the first entry is visible after populating
        self._listbox.yview_moveto(0)
        # Clear the "Loading…" placeholder from the preview pane
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.configure(state=tk.DISABLED)

    def _on_filter_change(self, *_) -> None:
        if self._filter_job is not None:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(_FILTER_DEBOUNCE_MS, self._do_filter)

    def _do_filter(self) -> None:
        self._filter_job = None
        self._apply_filter(self._filter_var.get())

    def _apply_filter(self, text: str) -> None:
        if text:
            pattern = _build_name_pattern(text)
            if pattern is None:
                # Invalid filter expression – show nothing
                self._filtered_indices = []
            else:
                self._filtered_indices = [
                    i for i, item in enumerate(self._all_items)
                    if self._filter_fn(item, text, pattern)
                    and (self._item_filter_fn is None or self._item_filter_fn(item))
                ]
        else:
            if self._item_filter_fn is not None:
                self._filtered_indices = [
                    i for i, item in enumerate(self._all_items)
                    if self._item_filter_fn(item)
                ]
            else:
                self._filtered_indices = list(range(len(self._all_items)))

        labels = [self._label_fn(self._all_items[i]) for i in self._filtered_indices]
        # Setting listvariable is faster than delete+insert for large lists
        self._list_var.set(labels)
        self._filter_count_var.set(f"{len(self._filtered_indices)} entries")

    def _on_select(self, _event=None) -> None:
        sel = self._listbox.curselection()
        if not sel:
            return
        list_pos = sel[0]
        if list_pos >= len(self._filtered_indices):
            return
        real_idx = self._filtered_indices[list_pos]
        item = self._all_items[real_idx]
        text = _truncate(json.dumps(item, indent=2))
        if self._extra_info_fn is not None:
            extra = self._extra_info_fn(item)
            if extra:
                text = text + "\n\n" + extra
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert(tk.END, text)
        self._preview.configure(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# Files tab: treeview panel
# ---------------------------------------------------------------------------

class _FsTreePanel(ttk.Frame):
    """Files tab: folder-hierarchy Treeview (left) + text preview (right).

    The tree is built from the Directive ``To`` paths.  Nodes are shown as
    folders (📁) when a later directive goes deeper under the same prefix,
    and as generic leaf entries (📄) otherwise.  Insertion order within each
    folder is preserved.

    Clicking any node shows:
    - "Directives affecting <path>" with up to 10 entries (first 5 / last 5).
    - The JSON of the last affecting directive.
    - For ``InlineFile`` directives: archive metadata and, when the
      uncompressed size is below 256 KiB, a text preview of the packed data.
    """

    def __init__(self, parent, **kwargs) -> None:
        super().__init__(parent, **kwargs)
        self._all_directives: list[tuple[str, dict]] = []  # (norm_path, directive)
        self._wabba: WabbaFile | None = None
        self._archives_by_hash: dict[str, dict] = {}
        self._build()

    def _build(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- left: treeview with scrollbars ---
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        self._tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self._tree.column("#0", width=350, stretch=True)
        vsb = ttk.Scrollbar(left, command=self._tree.yview)
        hsb = ttk.Scrollbar(left, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        # --- right: preview text area ---
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        self._preview = tk.Text(
            right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        sb2 = ttk.Scrollbar(right, command=self._preview.yview)
        self._preview.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self._preview.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------

    def set_loading(self) -> None:
        """Show a 'Loading…' placeholder in the tree while data is being fetched."""
        self._tree.delete(*self._tree.get_children())
        self._all_directives = []
        self._tree.insert("", tk.END, text="Loading…")

    def load_directives(self, directives: list, wabba, archives: list | None = None) -> None:
        """Rebuild the tree from *directives* (list of Directive dicts)."""
        self._wabba = wabba
        self._archives_by_hash = {
            a["Hash"]: a for a in (archives or []) if isinstance(a, dict) and "Hash" in a
        }
        self._tree.delete(*self._tree.get_children())
        self._all_directives = []

        for d in directives:
            if not isinstance(d, dict):
                continue
            to = d.get("To", "")
            if not to:
                continue
            norm = to.replace("\\", "/").rstrip("/")
            if norm:
                self._all_directives.append((norm, d))

        # Determine which paths are folders (have at least one deeper directive)
        folder_paths: set[str] = set()
        for norm, _ in self._all_directives:
            parts = norm.split("/")
            for i in range(1, len(parts)):
                folder_paths.add("/".join(parts[:i]))

        # Collect every unique path that needs a tree node.
        all_paths: set[str] = set()
        for norm, _ in self._all_directives:
            parts = norm.split("/")
            for i in range(1, len(parts) + 1):
                all_paths.add("/".join(parts[:i]))

        # Sort key: at each path component level, folders (0) sort before files (1),
        # then alphabetically. Shorter prefix tuples compare as less than longer ones
        # with the same prefix, so parents always appear before their children.
        def _sort_key(path: str):
            parts = path.split("/")
            return [
                (0 if "/".join(parts[: i + 1]) in folder_paths else 1, parts[i].lower())
                for i in range(len(parts))
            ]

        # Insert nodes using "end" (O(1)) because the sort guarantees order.
        for path in sorted(all_paths, key=_sort_key):
            parts = path.split("/")
            name = parts[-1]
            parent_iid = "/".join(parts[:-1])  # "" for top-level
            self._tree.insert(parent_iid, "end", iid=path, text=name)

    # ------------------------------------------------------------------

    def _on_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        self._show_preview(sel[0])

    def _show_preview(self, path: str) -> None:
        """Build and display the preview for the node at *path*."""
        # Affecting directives: exact match, ancestor, or descendant
        affecting = [
            (norm, d)
            for norm, d in self._all_directives
            if norm == path
            or path.startswith(norm + "/")
            or norm.startswith(path + "/")
        ]
        total = len(affecting)

        lines: list[str] = [f"{total} Directive(s) affecting \"{path}\" :", ""]
        if total == 0:
            lines.append("(none)")
        elif total <= 10:
            for _, d in affecting:
                lines.append(f"{d.get('To', '?')} [{d.get('Hash', '?')}]")
        else:
            for _, d in affecting[:5]:
                lines.append(f"{d.get('To', '?')} [{d.get('Hash', '?')}]")
            lines.append(f"... [{total} total]")
            for _, d in affecting[-5:]:
                lines.append(f"{d.get('To', '?')} [{d.get('Hash', '?')}]")

        if affecting:
            _, last_d = affecting[-1]
            lines.append("")
            lines.append(json.dumps(last_d, indent=2))

            if last_d.get("$type") in ("InlineFile", "RemappedInlineFile") and self._wabba is not None:
                last_type = last_d.get("$type", "InlineFile")
                source_id = last_d.get("SourceDataID", "")
                if source_id:
                    lines.append("")
                    try:
                        info = self._wabba.get_zip_info(source_id)
                        lines.append(f"[{last_type}] Archive entry: {source_id}")
                        lines.append(
                            f"  Uncompressed size : {info.file_size:,} bytes"
                        )
                        lines.append(
                            f"  Compressed size   : {info.compress_size:,} bytes"
                        )
                        crc_b64 = base64.b64encode(
                            info.CRC.to_bytes(4, "little")
                        ).decode()
                        lines.append(
                            f"  CRC               : {info.CRC:#010x}  ({crc_b64})"
                        )
                        data: bytes | None = None
                        if info.file_size < _INLINE_PREVIEW_MAX:
                            data = self._wabba.read_bytes(source_id)
                        if info.file_size <= _INLINE_WABBAHASH_MAX:
                            if data is not None:
                                wabba_hash = WabbaHashXX64(data)
                            else:
                                with self._wabba.open_member(source_id) as stream:
                                    wabba_hash = WabbaHashXX64_stream(stream)
                            last_hash_raw = last_d.get("Hash", "")
                            last_hash = (
                                last_hash_raw
                                if isinstance(last_hash_raw, str)
                                else ""
                            )
                            if last_hash:
                                match_note = (
                                    "[matches last directive Hash]"
                                    if wabba_hash == last_hash
                                    else "[does not match last directive Hash]"
                                )
                            else:
                                match_note = "[last directive has no Hash key]"
                            lines.append(
                                f"  WabbaHashXX64     : {wabba_hash}  {match_note}"
                            )
                        else:
                            lines.append(
                                "  WabbaHashXX64     : (WabbaHash for large files not yet implemented)"
                            )
                        if info.file_size < _INLINE_PREVIEW_MAX:
                            if data is None:
                                data = self._wabba.read_bytes(source_id)
                            raw_text = data.decode("latin-1", errors="replace")
                            clean = "".join(
                                c if c.isprintable() or c in "\n\r\t" else "?"
                                for c in raw_text
                            )
                            lines.append(
                                f"\n--- File preview ({info.file_size:,} bytes) ---"
                            )
                            lines.append(clean)
                    except FileNotFoundError:
                        lines.append(
                            f"[{last_type}] Source file '{source_id}' not found in archive"
                        )

            if last_d.get("$type") == "FromArchive":
                archive_hash_path = last_d.get("ArchiveHashPath")
                h = (archive_hash_path[0] if archive_hash_path else None) or last_d.get("Hash", "")
                if h and h in self._archives_by_hash:
                    archive_entry = self._archives_by_hash[h]
                    lines.append("")
                    lines.append("[FromArchive] Matching Archives entry:")
                    lines.append(json.dumps(archive_entry, indent=2))
                elif h:
                    lines.append("")
                    lines.append(f"[FromArchive] Hash '{h}' not found in Archives")

            if last_d.get("$type") == "PatchedFromArchive":
                archive_hash_path = last_d.get("ArchiveHashPath")
                h = (archive_hash_path[0] if archive_hash_path else None) or last_d.get("Hash", "")
                if h and h in self._archives_by_hash:
                    archive_entry = self._archives_by_hash[h]
                    lines.append("")
                    lines.append("[PatchedFromArchive] Matching Archives entry:")
                    lines.append(json.dumps(archive_entry, indent=2))
                elif h:
                    lines.append("")
                    lines.append(f"[PatchedFromArchive] Hash '{h}' not found in Archives")
                patch_id = last_d.get("PatchID", "")
                if patch_id and self._wabba is not None:
                    lines.append("")
                    try:
                        info = self._wabba.get_zip_info(patch_id)
                        lines.append(f"[PatchID] Archive entry: {patch_id}")
                        lines.append(f"  Uncompressed size : {info.file_size:,} bytes")
                        lines.append(f"  Compressed size   : {info.compress_size:,} bytes")
                        crc_b64 = base64.b64encode(info.CRC.to_bytes(4, "little")).decode()
                        lines.append(f"  CRC               : {info.CRC:#010x}  ({crc_b64})")
                        patch_data: bytes | None = None
                        if info.file_size < _INLINE_PREVIEW_MAX:
                            patch_data = self._wabba.read_bytes(patch_id)
                        if info.file_size <= _INLINE_WABBAHASH_MAX:
                            if patch_data is not None:
                                wabba_hash = WabbaHashXX64(patch_data)
                            else:
                                with self._wabba.open_member(patch_id) as stream:
                                    wabba_hash = WabbaHashXX64_stream(stream)
                            lines.append(f"  WabbaHashXX64     : {wabba_hash}")
                        else:
                            lines.append(
                                "  WabbaHashXX64     : (WabbaHash for large files not yet implemented)"
                            )
                        if info.file_size < _INLINE_PREVIEW_MAX:
                            if patch_data is None:
                                patch_data = self._wabba.read_bytes(patch_id)
                            raw_text = patch_data.decode("latin-1", errors="replace")
                            clean = "".join(
                                c if c.isprintable() or c in "\n\r\t" else "?"
                                for c in raw_text
                            )
                            lines.append(f"\n--- File preview ({info.file_size:,} bytes) ---")
                            lines.append(clean)
                    except FileNotFoundError:
                        lines.append(f"[PatchID] '{patch_id}' not found in wabba archive")

        text = "\n".join(lines)
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert(tk.END, text)
        self._preview.configure(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# Problems tab: mismatches tree + preview + progress
# ---------------------------------------------------------------------------

class _ProblemsPanel(_FsTreePanel):
    """Problems tab panel for InlineFile/RemappedInlineFile hash mismatches."""

    def __init__(self, parent, **kwargs) -> None:
        self._progress = None
        self._progress_var = tk.StringVar(value="")
        self._last_mismatch_count = -1
        self._stored_text = ""
        self.problem_report_lines: list[str] = []
        super().__init__(parent, **kwargs)

    def add_problem_report_line(self, line: str) -> None:
        """Append *line* to problem_report_lines and the right-side text widget."""
        self.problem_report_lines.append(line)
        self._stored_text = "\n".join(self.problem_report_lines)
        self._preview.configure(state=tk.NORMAL)
        self._preview.insert(tk.END, line + "\n")
        self._preview.see(tk.END)
        self._preview.configure(state=tk.DISABLED)

    def _build(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        self._tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self._tree.column("#0", width=350, stretch=True)
        vsb = ttk.Scrollbar(left, command=self._tree.yview)
        hsb = ttk.Scrollbar(left, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        self._preview = tk.Text(
            right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        sb2 = ttk.Scrollbar(right, command=self._preview.yview)
        self._preview.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self._preview.pack(fill=tk.BOTH, expand=True)

        progress_row = ttk.Frame(right)
        progress_row.pack(fill=tk.X, pady=(4, 0))
        self._progress = ttk.Progressbar(progress_row, mode="determinate", maximum=1)
        self._progress.pack(fill=tk.X)
        ttk.Label(progress_row, textvariable=self._progress_var, anchor=tk.W).pack(
            fill=tk.X, pady=(2, 0)
        )

    def set_analyzing(self, *, header: str = "") -> None:
        self._tree.delete(*self._tree.get_children())
        self._all_directives = []
        self.problem_report_lines = []
        self._stored_text = ""
        self._tree.insert("", tk.END, iid=_PROBLEMS_IID, text="PROBLEMS")
        # Clear the text area, then write the header (if any) via the report line method
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.configure(state=tk.DISABLED)
        if header:
            self.add_problem_report_line(header)
        if self._progress is not None:
            self._progress.configure(maximum=1, value=0)
        self._progress_var.set("analyzing...")
        self._last_mismatch_count = -1

    def load_directives(self, directives, wabba, archives=None) -> None:
        """Rebuild tree from mismatch directives, keeping PROBLEMS at the top."""
        super().load_directives(directives, wabba, archives)
        # super() clears the tree; re-insert the PROBLEMS summary node first
        self._tree.insert("", 0, iid=_PROBLEMS_IID, text="PROBLEMS")

    def _on_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        if sel[0] == _PROBLEMS_IID:
            self._set_preview(self._stored_text or "(analysis not yet complete)")
            return
        self._show_preview(sel[0])

    def update_analysis(
        self,
        *,
        total: int,
        processed: int,
        matches: int,
        mismatches: int,
        ignores: int,
        elapsed: float,
        mismatch_directives: list[dict],
        wabba: WabbaFile | None,
        archives: list | None,
        done: bool,
        unused_archives: list | None = None,
        missing_archives: list[str] | None = None,
        missing_inline_files: list[str] | None = None,
    ) -> None:
        total_safe = total if total > 0 else 1
        if self._progress is not None:
            self._progress.configure(maximum=total_safe, value=min(processed, total_safe))

        def _pct(value: int) -> float:
            return (100.0 * value / total) if total else 0.0

        progress_text = (
            f"processed {processed}/{total} | "
            f"matches {matches} ({_pct(matches):.1f}%) | "
            f"mismatches {mismatches} ({_pct(mismatches):.1f}%) | "
            f"ignored {ignores} ({_pct(ignores):.1f}%) | "
            f"elapsed {elapsed:.1f}s"
        )
        self._progress_var.set(progress_text)

        if mismatches != self._last_mismatch_count or done:
            self.load_directives(mismatch_directives, wabba, archives)
            self._last_mismatch_count = mismatches

        if not done:
            return

        # Build structured report at completion
        self.add_problem_report_line("")
        self.add_problem_report_line("Directives:")
        if mismatch_directives:
            for item in mismatch_directives:
                self.add_problem_report_line(f"- hash mismatch: {_directive_label(item)}")
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Unused Archives:")
        if unused_archives:
            for a in unused_archives:
                self.add_problem_report_line(f"- unused: {_archive_label(a)}")
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Missing Archives:")
        if missing_archives:
            for line in missing_archives:
                self.add_problem_report_line(line)
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Missing InlineFiles:")
        if missing_inline_files:
            for line in missing_inline_files:
                self.add_problem_report_line(line)
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Summary:")
        self.add_problem_report_line(f"- processed total: {total}")
        self.add_problem_report_line(f"- hash matches: {matches} ({_pct(matches):.1f}%)")
        self.add_problem_report_line(f"- mismatches: {mismatches} ({_pct(mismatches):.1f}%)")
        self.add_problem_report_line(f"- ignored: {ignores} ({_pct(ignores):.1f}%)")
        self.add_problem_report_line(f"- elapsed: {elapsed:.1f}s")

    def _set_preview(self, text: str) -> None:
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert(tk.END, text)
        self._preview.configure(state=tk.DISABLED)


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

class WabbaExplorerApp(tk.Tk):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"Wabba Explorer {__version__}")
        self.geometry("1100x750")
        self.minsize(700, 500)

        self._wabba: WabbaFile | None = None
        self._modlist_data: dict | None = None
        self._modlist_keys: list[str] = []
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        self._outer_paned: ttk.PanedWindow | None = None

        # Pending data for lazy tab population (None = nothing waiting)
        self._pending_archives: list | None = None
        self._pending_directives: list | None = None
        self._pending_files_args: tuple | None = None  # (directives, wabba, archives)
        self._all_directives_list: list = []  # full directives list for cross-tab lookup
        self._archives_by_hash: dict[str, dict] = {}  # hash → archive entry
        self._recent_files: list[str] = []
        self._max_recent_files = 8
        self._recent_files_menu: tk.Menu | None = None
        self._problems_run_id = 0
        self._problems_analysis_started = False

        self._load_recent_files()
        self._build_ui()
        self._build_menubar()
        self._redirect_output_streams()
        self.after(100, self._init_sash)

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

    def _show_about(self) -> None:
        _XXHASH_LICENSE = """\
xxHash — Extremely fast hash algorithm
Copyright (C) 2012-2023 Yann Collet

BSD 2-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

* Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in
  the documentation and/or other materials provided with the
  distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.

Source: https://github.com/ifduyue/python-xxhash
        https://github.com/Cyan4973/xxHash
License: https://opensource.org/licenses/BSD-2-Clause
"""
        win = tk.Toplevel(self)
        win.title("About – Wabba Explorer")
        win.resizable(True, True)
        win.geometry("620x420")
        win.grab_set()

        text = tk.Text(win, wrap=tk.WORD, font=("Consolas", 9), padx=8, pady=8)
        sb = ttk.Scrollbar(win, command=text.yview)
        text.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(tk.END, _XXHASH_LICENSE)
        text.configure(state=tk.DISABLED)

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)
        win.bind("<Escape>", lambda _e: win.destroy())

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # ---- status bar (packed first → always at the bottom) --------
        self._status_var = tk.StringVar(value="Ready – open a .wabbajack file.")
        ttk.Label(
            self, textvariable=self._status_var, anchor=tk.W, relief=tk.SUNKEN
        ).pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=2)

        # ---- outer vertical paned (main tabs above, console below) ---
        outer = ttk.PanedWindow(self, orient=tk.VERTICAL)
        outer.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._outer_paned = outer

        # ---- main notebook (three content tabs) ----------------------
        self._main_nb = ttk.Notebook(outer)
        outer.add(self._main_nb, weight=4)

        self._build_tab_modlist_json()
        self._build_tab_file_explorer()
        self._build_tab_archives()
        self._build_tab_directives()
        self._build_tab_problems()
        self._main_nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        # ---- bottom notebook (Console tab) ---------------------------
        bottom_nb = ttk.Notebook(outer)
        outer.add(bottom_nb, weight=1)

        console_frame = ttk.Frame(bottom_nb)
        bottom_nb.add(console_frame, text="Console")

        self._console_text = tk.Text(
            console_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 9),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        console_scroll = ttk.Scrollbar(console_frame, command=self._console_text.yview)
        self._console_text.configure(yscrollcommand=console_scroll.set)
        console_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._console_text.pack(fill=tk.BOTH, expand=True)
        self._console_text.bind("<Control-c>", self._console_copy)
        self._console_text.bind("<Button-3>", self._console_right_click)

    def _build_tab_modlist_json(self) -> None:
        """Tab 1: key list on the left, text preview on the right."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="modlist json")

        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left: key list
        left = ttk.LabelFrame(paned, text="modlist keys", padding=4)
        paned.add(left, weight=1)

        self._key_listbox = tk.Listbox(left, activestyle="dotbox")
        left_scroll = ttk.Scrollbar(left, command=self._key_listbox.yview)
        self._key_listbox.configure(yscrollcommand=left_scroll.set)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._key_listbox.pack(fill=tk.BOTH, expand=True)
        self._key_listbox.bind("<<ListboxSelect>>", self._on_key_select)

        # Right: text preview
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

    def _build_tab_archives(self) -> None:
        """Tab 2: Archives list (Name [Hash]) with filter + JSON preview."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="Archives")

        self._archives_panel = _FilteredListPanel(
            frame,
            label_fn=_archive_label,
            filter_fn=lambda item, t, pat: _item_matches(item, t, pat, "Name", "Hash"),
            extra_info_fn=self._archive_extra_info,
        )
        self._archives_panel.pack(fill=tk.BOTH, expand=True)

    def _archive_extra_info(self, archive_item: dict) -> str:
        """Return extra preview text listing directives that reference this archive."""
        archive_hash = archive_item.get("Hash", "")
        if not archive_hash:
            return ""
        matches = []
        for d in self._all_directives_list:
            if not isinstance(d, dict):
                continue
            ahp = d.get("ArchiveHashPath")
            lookup_hash = (ahp[0] if ahp else None) or d.get("Hash", "")
            if lookup_hash == archive_hash:
                matches.append(d)
        total = len(matches)
        if total == 0:
            return ""
        def _fmt(d: dict) -> str:
            to = d.get("To", "(no To)")
            h = d.get("Hash", "")
            return f"  {to} [{h}]" if h else f"  {to}"

        lines = [f"{total} directive(s) using this archive:"]
        if total <= 10:
            for d in matches:
                lines.append(_fmt(d))
        else:
            for d in matches[:5]:
                lines.append(_fmt(d))
            lines.append(f"  … ({total - 10} more) …")
            for d in matches[-5:]:
                lines.append(_fmt(d))
        return "\n".join(lines)

    def _build_tab_file_explorer(self) -> None:
        """Tab: virtual filesystem tree built from Directive 'To' paths."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="Files")

        self._files_panel = _FsTreePanel(frame)
        self._files_panel.pack(fill=tk.BOTH, expand=True)

    def _build_tab_directives(self) -> None:
        """Tab 3: Directives list (To [Hash]) with filter + JSON preview."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="Directives")

        # Checkbox vars for type filter (created here so they exist before any
        # load_items call; the panel's extra_controls_fn will reference them).
        self._dir_show_inline = tk.BooleanVar(value=True)
        self._dir_show_fromarchive = tk.BooleanVar(value=True)
        self._dir_show_patchedfromarchive = tk.BooleanVar(value=True)
        self._dir_show_other = tk.BooleanVar(value=True)

        def _type_controls(left_frame: ttk.Frame) -> None:
            cb_frame = ttk.Frame(left_frame)
            cb_frame.pack(fill=tk.X, pady=(2, 0))
            for text, var in (
                ("InlineFile", self._dir_show_inline),
                ("FromArchive", self._dir_show_fromarchive),
                ("PatchedFromArchive", self._dir_show_patchedfromarchive),
                ("Other", self._dir_show_other),
            ):
                ttk.Checkbutton(
                    cb_frame, text=text, variable=var,
                    command=self._on_directive_type_filter_change,
                ).pack(side=tk.LEFT)

        def _type_gate(item: dict) -> bool:
            t = item.get("$type", "")
            if t == "InlineFile":
                return self._dir_show_inline.get()
            if t == "FromArchive":
                return self._dir_show_fromarchive.get()
            if t == "PatchedFromArchive":
                return self._dir_show_patchedfromarchive.get()
            return self._dir_show_other.get()

        def _directive_extra_info(item: dict) -> str:
            """Delegate to the app-level method so it can access wabba/archives."""
            return self._directive_detail(item)

        self._directives_panel = _FilteredListPanel(
            frame,
            label_fn=_directive_label,
            filter_fn=lambda item, t, pat: _item_matches(item, t, pat, "To", "Hash"),
            extra_controls_fn=_type_controls,
            item_filter_fn=_type_gate,
            extra_info_fn=_directive_extra_info,
        )
        self._directives_panel.pack(fill=tk.BOTH, expand=True)

    def _build_tab_problems(self) -> None:
        """Tab: directive hash mismatch analysis with progress."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="Problems")

        self._problems_panel = _ProblemsPanel(frame)
        self._problems_panel.pack(fill=tk.BOTH, expand=True)

    def _redirect_output_streams(self) -> None:
        sys.stdout = _StdoutRedirect(self._console_text, self._orig_stdout)
        sys.stderr = _StdoutRedirect(self._console_text, self._orig_stderr)

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_directive_type_filter_change(self) -> None:
        """Re-apply the filter whenever a $type checkbox is toggled."""
        self._directives_panel._do_filter()  # noqa: SLF001

    def _show_wabba_entry(
        self,
        source_id: str,
        label: str,
        lines: list[str],
        compare_hash: str = "",
    ) -> None:
        """Append wabba-archive entry info (size, CRC, WabbaHashXX64, preview) to *lines*.

        *source_id* is the filename key inside the wabba zip.
        *label*     is a human-readable prefix shown in the header line.
        *compare_hash* is an optional hash string to compare against WabbaHashXX64.
        Raises FileNotFoundError if the entry is not found (caller should catch).
        """
        assert self._wabba is not None  # guard; callers check before calling
        info = self._wabba.get_zip_info(source_id)
        lines.append(f"[{label}] Archive entry: {source_id}")
        lines.append(f"  Uncompressed size : {info.file_size:,} bytes")
        lines.append(f"  Compressed size   : {info.compress_size:,} bytes")
        crc_b64 = base64.b64encode(info.CRC.to_bytes(4, "little")).decode()
        lines.append(f"  CRC               : {info.CRC:#010x}  ({crc_b64})")
        data: bytes | None = None
        if info.file_size < _INLINE_PREVIEW_MAX:
            data = self._wabba.read_bytes(source_id)
        if info.file_size <= _INLINE_WABBAHASH_MAX:
            if data is not None:
                wabba_hash = WabbaHashXX64(data)
            else:
                with self._wabba.open_member(source_id) as stream:
                    wabba_hash = WabbaHashXX64_stream(stream)
            if compare_hash:
                match_note = (
                    "[matches directive Hash]"
                    if wabba_hash == compare_hash
                    else "[does not match directive Hash]"
                )
            else:
                match_note = "[no Hash to compare]"
            lines.append(f"  WabbaHashXX64     : {wabba_hash}  {match_note}")
        else:
            lines.append(
                "  WabbaHashXX64     : (WabbaHash for large files not yet implemented)"
            )
        if info.file_size < _INLINE_PREVIEW_MAX:
            if data is None:
                data = self._wabba.read_bytes(source_id)
            raw_text = data.decode("latin-1", errors="replace")
            clean = "".join(
                c if c.isprintable() or c in "\n\r\t" else "?"
                for c in raw_text
            )
            lines.append(f"\n--- File preview ({info.file_size:,} bytes) ---")
            lines.append(clean)

    def _directive_detail(self, item: dict) -> str:
        """Rich extra-info for the Directives tab detail pane.

        - FromArchive / PatchedFromArchive: resolve archive hash and show the
          matching Archives entry (full JSON) from the modlist.
        - Any directive with a "PatchID" key: look up the PatchID as a filename
          inside the wabba archive and show WabbaHashXX64 + preview.
        - InlineFile: show zip-entry metadata, WabbaHash, and a text preview.
        """
        t = item.get("$type", "")
        lines: list[str] = []

        if t in ("FromArchive", "PatchedFromArchive"):
            ahp = item.get("ArchiveHashPath")
            h = (ahp[0] if ahp else None) or item.get("Hash", "")
            if h and h in self._archives_by_hash:
                archive_entry = self._archives_by_hash[h]
                lines.append(f"[{t}] Matching Archives entry:")
                lines.append(json.dumps(archive_entry, indent=2))
            elif h:
                lines.append(f"[{t}] Hash '{h}' not found in Archives")

        elif t in ("InlineFile", "RemappedInlineFile") and self._wabba is not None:
            source_id = item.get("SourceDataID", "")
            if source_id:
                try:
                    self._show_wabba_entry(
                        source_id,
                        t,
                        lines,
                        compare_hash=item.get("Hash", "") or "",
                    )
                except FileNotFoundError:
                    lines.append(
                        f"[{t}] Source file '{source_id}' not found in archive"
                    )

        # PatchID: present on any directive type (most commonly PatchedFromArchive).
        # Display it like an InlineFile — search by filename in the wabba archive.
        patch_id = item.get("PatchID", "")
        if patch_id and self._wabba is not None:
            if lines:
                lines.append("")  # blank separator
            try:
                self._show_wabba_entry(patch_id, "PatchID", lines)
            except FileNotFoundError:
                lines.append(f"[PatchID] '{patch_id}' not found in wabba archive")

        return "\n".join(lines)

    def _init_sash(self) -> None:
        """Place the sash so the console takes ~20 % of the paned area.

        This matches the natural 4:1 weight ratio (main notebook weight=4,
        console weight=1) and gives the console a usable default height.
        """
        if self._outer_paned is None:
            return
        total = self._outer_paned.winfo_height()
        if total > 1:
            self._outer_paned.sashpos(0, int(total * 0.80))

    def _on_open(self) -> None:
        path = filedialog.askopenfilename(
            title="Open .wabbajack file",
            filetypes=[("Wabbajack archives", "*.wabbajack"), ("All files", "*.*")],
        )
        if not path:
            return
        self._load_file(path)

    def _load_file(self, path: str) -> None:
        if self._wabba is not None:
            self._wabba.close()
            self._wabba = None

        wabba = WabbaFile(path)
        try:
            wabba.open()
        except Exception as exc:
            messagebox.showerror("Error", f"Could not open file:\n{exc}")
            return

        self._wabba = wabba
        self._remember_recent_file(path)
        self._modlist_data = None
        self._modlist_keys = []

        # Clear any pending lazy-load data from a previous file
        self._pending_archives = None
        self._pending_directives = None
        self._pending_files_args = None
        self._problems_run_id += 1
        self._problems_analysis_started = False

        # Show loading placeholders before heavy parsing so the UI responds
        self._status_var.set(f"Loading: {path} …")
        self._key_listbox.delete(0, tk.END)
        self._key_listbox.insert(tk.END, "Loading…")
        self._archives_panel.set_loading()
        self._directives_panel.set_loading()
        self._files_panel.set_loading()
        _report_header = "\n".join([
            f"Wabba Explorer {__version__} problem report for {os.path.basename(path)}",
            f"Path: {path}",
        ])
        self._problems_panel.set_analyzing(header=_report_header)
        # Force a full repaint so the loading placeholders are visible before
        # the heavy parsing begins (update_idletasks alone is insufficient
        # because it doesn't process expose/redraw events on all platforms).
        self.update()

        # Offload all heavy I/O and parsing to a background thread so the
        # Tkinter event loop stays responsive.  UI state is only touched from
        # the main thread via self.after().  The 100 ms delay lets the event
        # loop process the repaint of the "Loading…" placeholders before work
        # begins.
        self.after(
            100,
            lambda: threading.Thread(
                target=self._load_file_worker,
                args=(path, wabba),
                daemon=True,
            ).start(),
        )

    def _load_file_worker(self, path: str, wabba: "WabbaFile") -> None:
        """Background thread: parse the modlist JSON and schedule UI updates."""
        modlist_data: dict | None = None
        modlist_keys: list[str] = []

        # Parse modlist JSON
        try:
            raw = wabba.read_modlist()
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    modlist_data = data
                    modlist_keys = list(data.keys())
                else:
                    print(
                        f"[wabba_explorer] modlist root is not a JSON object "
                        f"(type={type(data).__name__})"
                    )
            except json.JSONDecodeError as exc:
                print(f"[wabba_explorer] modlist JSON parse error: {exc}")
        except FileNotFoundError:
            print("[wabba_explorer] 'modlist' entry not found in archive")

        archives: list = []
        directives: list = []
        if modlist_data:
            raw_archives = modlist_data.get("Archives", [])
            archives = raw_archives if isinstance(raw_archives, list) else []
            raw_directives = modlist_data.get("Directives", [])
            directives = raw_directives if isinstance(raw_directives, list) else []

        # Schedule UI population back on the main thread
        self.after(0, self._populate_ui, path, wabba, modlist_data, modlist_keys, archives, directives)

    def _populate_ui(
        self,
        path: str,
        wabba: "WabbaFile",
        modlist_data: dict | None,
        modlist_keys: list[str],
        archives: list,
        directives: list,
    ) -> None:
        """Main-thread callback: populate all tabs with parsed data."""
        self._modlist_data = modlist_data
        self._modlist_keys = modlist_keys

        # Append version line to Problems header now that modlist is parsed
        version = (modlist_data.get("Version", "") if modlist_data else "") or "unknown"
        self._problems_panel.add_problem_report_line(f"Version: {version}")

        # Populate tab 1 key list (fast – just a handful of top-level keys)
        self._key_listbox.delete(0, tk.END)
        if self._modlist_data:
            for key in self._modlist_keys:
                self._key_listbox.insert(tk.END, _key_label(key, self._modlist_data[key]))

        # Store heavy-tab data for lazy population on first tab-switch.
        # The panels already show "Loading…" from _load_file(); they stay
        # that way until the user actually opens each tab.
        self._all_directives_list = directives
        self._archives_by_hash = {
            a["Hash"]: a for a in archives if isinstance(a, dict) and "Hash" in a
        }
        self._pending_archives = archives
        self._pending_directives = directives
        self._pending_files_args = (directives, wabba, archives)
        print(f"[wabba_explorer] Archives: {len(archives)} entries (lazy)")
        print(f"[wabba_explorer] Directives: {len(directives)} entries (lazy)")

        n = len(self._modlist_keys)
        self._status_var.set(f"Opened: {path}  ({n} modlist keys)")
        print(f"[wabba_explorer] Opened '{path}' – {n} modlist top-level keys")

        self._set_content("Select a key on the left to preview its contents.")
        self._content_label.configure(text="Preview")

        # If the user is already on one of the heavy tabs, trigger its load now.
        self._on_tab_changed()

    def _on_tab_changed(self, _event=None) -> None:
        """Lazy-populate a heavy tab the first time it is selected.

        Called both by the ``<<NotebookTabChanged>>`` binding and directly
        from ``_populate_ui`` (to handle the case where the user is already
        on a heavy tab when the file finishes loading).

        For whichever heavy tab is currently active and has pending data:
        1. ``set_loading()`` is called so "Loading…" is immediately visible.
        2. The actual population is scheduled 100 ms later, giving Tkinter
           time to repaint the placeholder before the blocking work starts.
        """
        try:
            tab_text = self._main_nb.tab(self._main_nb.select(), "text")
        except tk.TclError:
            return

        if tab_text == "Archives" and self._pending_archives is not None:
            data = self._pending_archives
            self._pending_archives = None
            self._archives_panel.set_loading()
            self.after(100, lambda: self._archives_panel.load_items(data))

        elif tab_text == "Directives" and self._pending_directives is not None:
            data = self._pending_directives
            self._pending_directives = None
            self._directives_panel.set_loading()
            # Print $type counts to console
            type_counts: Counter = Counter()
            for d in data:
                if isinstance(d, dict):
                    type_counts[d.get("$type", "(none)")] += 1
            print(f"[wabba_explorer] Directives $type counts:")
            for dtype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
                print(f"  {dtype}: {count}")
            self.after(100, lambda: self._directives_panel.load_items(data))

        elif tab_text == "Files" and self._pending_files_args is not None:
            args = self._pending_files_args
            self._pending_files_args = None
            self._files_panel.set_loading()
            self.after(100, lambda: self._files_panel.load_directives(*args))

        elif tab_text == "Problems":
            if not self._problems_analysis_started:
                self._start_problems_analysis()

    def _start_problems_analysis(self) -> None:
        self._problems_run_id += 1
        run_id = self._problems_run_id
        self._problems_analysis_started = True
        # The panel was already initialised with header in _load_file; no reset here.

        wabba = self._wabba
        directives = list(self._all_directives_list)
        archives = list(self._archives_by_hash.values())
        self.after(
            100,
            lambda: threading.Thread(
                target=self._run_problems_analysis_worker,
                args=(run_id, wabba, directives, archives),
                daemon=True,
            ).start(),
        )

    def _run_problems_analysis_worker(
        self,
        run_id: int,
        wabba: WabbaFile | None,
        directives: list,
        archives: list,
    ) -> None:
        total = len(directives)
        processed = 0
        matches = 0
        mismatches = 0
        ignores = 0
        mismatch_directives: list[dict] = []
        used_hashes: set[str] = set()
        missing_archives: list[str] = []
        missing_inline_files: list[str] = []
        # Build set of archive hashes for fast lookup
        archives_by_hash: set[str] = {
            a.get("Hash", "") for a in archives if isinstance(a, dict) and a.get("Hash", "")
        }
        # Build set of wabba zip root filenames for fast lookup
        wabba_root_names: set[str] = set()
        if wabba is not None:
            try:
                wabba_root_names = set(wabba.list_root_files())
            except Exception:
                pass
        start = time.monotonic()
        last_update = start

        self.after(
            0,
            lambda: self._update_problems_ui(
                run_id,
                total=total,
                processed=0,
                matches=0,
                mismatches=0,
                ignores=0,
                elapsed=0.0,
                mismatch_directives=[],
                wabba=wabba,
                archives=archives,
                done=False,
            ),
        )

        for d in directives:
            if run_id != self._problems_run_id:
                return
            processed += 1
            if not isinstance(d, dict):
                ignores += 1
            else:
                dtype = d.get("$type", "")
                # Track which archive hashes are referenced by FromArchive / PatchedFromArchive
                if dtype in ("FromArchive", "PatchedFromArchive"):
                    ahp = d.get("ArchiveHashPath")
                    h = (ahp[0] if ahp else None) or d.get("Hash", "")
                    if h:
                        used_hashes.add(h)

                if dtype == "InlineFile":
                    # Check for hash mismatch (RemappedInlineFile excluded – see below)
                    expected_hash = d.get("Hash", "")
                    source_id = d.get("SourceDataID", "")
                    actual_hash = ""
                    if wabba is not None and isinstance(source_id, str) and source_id:
                        try:
                            with wabba.open_member(source_id) as stream:
                                actual_hash = WabbaHashXX64_stream(stream)
                        except FileNotFoundError:
                            actual_hash = ""
                    if expected_hash and actual_hash and expected_hash == actual_hash:
                        matches += 1
                    else:
                        mismatches += 1
                        mismatch_directives.append(d)
                    # Check SourceDataID exists in wabba zip root
                    if isinstance(source_id, str) and source_id:
                        if source_id not in wabba_root_names:
                            to = d.get("To", source_id)
                            missing_inline_files.append(
                                f"- missing InlineFile: {to} [SourceDataID={source_id}]"
                            )

                elif dtype == "RemappedInlineFile":
                    # Not checked for hash mismatches but SourceDataID should exist
                    source_id = d.get("SourceDataID", "")
                    if isinstance(source_id, str) and source_id:
                        if source_id not in wabba_root_names:
                            to = d.get("To", source_id)
                            missing_inline_files.append(
                                f"- missing InlineFile: {to} [SourceDataID={source_id}]"
                            )
                    ignores += 1

                elif dtype == "FromArchive":
                    # ArchiveHashPath[0] must exist in Archives by hash
                    ahp = d.get("ArchiveHashPath")
                    h = (ahp[0] if ahp else None) or d.get("Hash", "")
                    if h and h not in archives_by_hash:
                        to = d.get("To", "?")
                        missing_archives.append(f"- missing Archive: {to} [hash={h}]")
                    ignores += 1

                elif dtype == "PatchedFromArchive":
                    # ArchiveHashPath[0] → Archives
                    ahp = d.get("ArchiveHashPath")
                    h = (ahp[0] if ahp else None) or d.get("Hash", "")
                    if h and h not in archives_by_hash:
                        to = d.get("To", "?")
                        missing_archives.append(f"- missing Archive: {to} [hash={h}]")
                    # PatchID → wabba zip root (treated as inline file)
                    patch_id = d.get("PatchID", "")
                    if isinstance(patch_id, str) and patch_id:
                        if patch_id not in wabba_root_names:
                            to = d.get("To", patch_id)
                            missing_inline_files.append(
                                f"- missing InlineFile: {to} [PatchID={patch_id}]"
                            )
                    ignores += 1

                else:
                    ignores += 1

            now = time.monotonic()
            if now - last_update >= _PROBLEMS_UPDATE_INTERVAL_SECS:
                snapshot = list(mismatch_directives)
                elapsed = now - start
                self._schedule_problems_update(
                    run_id=run_id,
                    total=total,
                    processed=processed,
                    matches=matches,
                    mismatches=mismatches,
                    ignores=ignores,
                    elapsed=elapsed,
                    mismatch_directives=snapshot,
                    wabba=wabba,
                    archives=archives,
                    done=False,
                )
                last_update = now

        elapsed = time.monotonic() - start
        unused_archives = [
            a for a in archives
            if isinstance(a, dict) and a.get("Hash", "") not in used_hashes
        ]
        self.after(
            0,
            lambda ua=unused_archives, ma=list(missing_archives), mi=list(missing_inline_files): (
                self._update_problems_ui(
                    run_id,
                    total=total,
                    processed=processed,
                    matches=matches,
                    mismatches=mismatches,
                    ignores=ignores,
                    elapsed=elapsed,
                    mismatch_directives=list(mismatch_directives),
                    wabba=wabba,
                    archives=archives,
                    done=True,
                    unused_archives=ua,
                    missing_archives=ma,
                    missing_inline_files=mi,
                )
            ),
        )

    def _schedule_problems_update(
        self,
        *,
        run_id: int,
        total: int,
        processed: int,
        matches: int,
        mismatches: int,
        ignores: int,
        elapsed: float,
        mismatch_directives: list[dict],
        wabba: WabbaFile | None,
        archives: list,
        done: bool,
    ) -> None:
        self.after(
            0,
            lambda: self._update_problems_ui(
                run_id,
                total=total,
                processed=processed,
                matches=matches,
                mismatches=mismatches,
                ignores=ignores,
                elapsed=elapsed,
                mismatch_directives=mismatch_directives,
                wabba=wabba,
                archives=archives,
                done=done,
            ),
        )

    def _update_problems_ui(
        self,
        run_id: int,
        *,
        total: int,
        processed: int,
        matches: int,
        mismatches: int,
        ignores: int,
        elapsed: float,
        mismatch_directives: list[dict],
        wabba: WabbaFile | None,
        archives: list,
        done: bool,
        unused_archives: list | None = None,
        missing_archives: list[str] | None = None,
        missing_inline_files: list[str] | None = None,
    ) -> None:
        if run_id != self._problems_run_id:
            return
        self._problems_panel.update_analysis(
            total=total,
            processed=processed,
            matches=matches,
            mismatches=mismatches,
            ignores=ignores,
            elapsed=elapsed,
            mismatch_directives=mismatch_directives,
            wabba=wabba,
            archives=archives,
            done=done,
            unused_archives=unused_archives,
            missing_archives=missing_archives,
            missing_inline_files=missing_inline_files,
        )

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

    # ------------------------------------------------------------------
    # Console helpers
    # ------------------------------------------------------------------

    def _console_copy(self, _event=None) -> str:
        try:
            selected = self._console_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(selected)
        except tk.TclError:
            pass
        return "break"

    def _console_right_click(self, event) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Copy selection", command=self._console_copy)
        menu.add_command(label="Select all", command=self._console_select_all)
        menu.tk_popup(event.x_root, event.y_root)

    def _console_select_all(self) -> None:
        self._console_text.tag_add(tk.SEL, "1.0", tk.END)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_content(self, text: str) -> None:
        self._content_text.configure(state=tk.NORMAL)
        self._content_text.delete("1.0", tk.END)
        self._content_text.insert(tk.END, text)
        self._content_text.configure(state=tk.DISABLED)

    def destroy(self) -> None:
        if isinstance(sys.stdout, _StdoutRedirect):
            sys.stdout = self._orig_stdout
        if isinstance(sys.stderr, _StdoutRedirect):
            sys.stderr = self._orig_stderr
        if self._wabba is not None:
            self._wabba.close()
        super().destroy()


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------

def _key_label(key: str, value) -> str:
    """Return a listbox label like 'Archives [30482]'."""
    if isinstance(value, (list, dict)):
        return f"{key} [{len(value)}]"
    return key


def _archive_label(item: dict) -> str:
    """Label for an Archives entry: 'Name [Hash]'."""
    if not isinstance(item, dict):
        return str(item)
    name = item.get("Name", "?")
    h = item.get("Hash", "?")
    return f"{name} [{h}]"


def _directive_label(item: dict) -> str:
    """Label for a Directives entry: 'To [Hash]'."""
    if not isinstance(item, dict):
        return str(item)
    to = item.get("To", "?")
    h = item.get("Hash", "?")
    return f"{to} [{h}]"


def _build_name_pattern(text: str) -> re.Pattern | None:
    """Translate a user filter string into a compiled regex for name/path fields.

    Rules:
    - ``^`` at the very start anchors the match to the beginning of the field.
    - ``*`` anywhere is a wildcard (matches any sequence of characters).
    - All other characters are treated as literals (re.escaped).
    - Match is case-insensitive.
    - Without ``^``, the pattern may match anywhere in the field value.

    Examples::

        "qt6"        → search for "qt6"  anywhere  (case-insensitive)
        "^mod"       → field must start with "mod"  (case-insensitive)
        "^start*mid" → field starts with "start", contains "mid" after it
    """
    anchored = text.startswith("^")
    raw = text[1:] if anchored else text
    pieces = raw.split("*")
    pattern = ".*".join(re.escape(p) for p in pieces)
    if anchored:
        pattern = "^" + pattern
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def _item_matches(item, text: str, pattern: re.Pattern, name_field: str, hash_field: str) -> bool:
    """Return True if *item* matches the filter *text*.

    *pattern* is the pre-compiled regex for the name/path field (built once per
    filter change by the caller via :func:`_build_name_pattern`).

    The filter text is tested against two fields:
    - *hash_field*: full exact match (case-sensitive, as hashes are base64).
    - *name_field*: regex match using the pre-compiled *pattern*.
    """
    if not isinstance(item, dict):
        return text.lower() in str(item).lower()
    # Hash: always full exact match (no partial, no case folding)
    if item.get(hash_field, "") == text:
        return True
    # Name/To: search using the pre-compiled pattern
    name_val = item.get(name_field, "")
    return pattern.search(name_val) is not None



def _truncate(s: str) -> str:
    if len(s) > _PREVIEW_MAX_CHARS:
        return s[:_PREVIEW_MAX_CHARS] + f"\n… (truncated, {len(s)} chars total)"
    return s


_SEP = "\n\n" + "─" * 60 + "\n\n"


def _preview_value(key: str, value) -> str:
    """Build a human-readable preview showing first 5 and last 5 sub-entries."""
    if isinstance(value, list):
        count = len(value)
        head = value[:_PREVIEW_HEAD]
        tail = value[max(count - _PREVIEW_TAIL, _PREVIEW_HEAD):] if count > _PREVIEW_HEAD else []

        header = (
            f"# {key}  —  list with {count} entr{'y' if count == 1 else 'ies'}"
            f"  (first {min(_PREVIEW_HEAD, count)}"
            + (f", last {len(tail)}" if tail else "")
            + ")\n\n"
        )
        parts = [_truncate(json.dumps(item, indent=2)) for item in head]
        if tail:
            skipped = count - len(head) - len(tail)
            parts.append(f"… {skipped} skipped …")
            parts += [_truncate(json.dumps(item, indent=2)) for item in tail]
        return header + _SEP.join(parts)

    if isinstance(value, dict):
        count = len(value)
        items = list(value.items())
        head = items[:_PREVIEW_HEAD]
        tail = items[max(count - _PREVIEW_TAIL, _PREVIEW_HEAD):] if count > _PREVIEW_HEAD else []

        header = (
            f"# {key}  —  object with {count} key{'s' if count != 1 else ''}"
            f"  (first {min(_PREVIEW_HEAD, count)}"
            + (f", last {len(tail)}" if tail else "")
            + ")\n\n"
        )
        parts = [_truncate(json.dumps({k: v}, indent=2)) for k, v in head]
        if tail:
            skipped = count - len(head) - len(tail)
            parts.append(f"… {skipped} skipped …")
            parts += [_truncate(json.dumps({k: v}, indent=2)) for k, v in tail]
        return header + _SEP.join(parts)

    s = _truncate(json.dumps(value, indent=2))
    return f"# {key}\n\n{s}"


def run_gui(initial_file: str | None = None, *, auto_open_recent: bool = False) -> None:
    """Launch the GUI.  Optionally open *initial_file* on start-up.

    When *auto_open_recent* is True and no *initial_file* is given, the most
    recent file from the persistent recent-files list is opened automatically.
    """
    print(f"wabba_explorer {__version__}")
    app = WabbaExplorerApp()
    if initial_file:
        app._load_file(initial_file)
    elif auto_open_recent and app._recent_files:
        app._load_file(app._recent_files[0])
    app.mainloop()
