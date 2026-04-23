"""Files-tab tree panel: folder-hierarchy Treeview + text preview (tkinter).

Children of a folder node are inserted lazily when the user expands it,
using a dummy placeholder child to display the expand arrow before the
real children are loaded.
"""

import time
import tkinter as tk
from tkinter import ttk

from ..wabba_file import WabbaFile
from ..wabba.entry_info import get_node_preview_lines
from ..wabba.cache import FS_FLAG_INLINE, FS_FLAG_FROM_ARCHIVE, FS_FLAG_PATCHED, FS_FLAG_OTHER, FS_FLAG_ALL
from .gui_util import _build_name_pattern, _get_extract_source_id, _do_extract_inline
from .tooltip import _Tooltip

# IID prefix for dummy placeholder children (must not collide with real paths)
_DUMMY_PREFIX = "__dummy__/"

_FILTER_DEBOUNCE_MS = 300
_FILTER_PLACEHOLDER = "^=start, *=wildcard"


class _FsTreePanel(ttk.Frame):
    """Files tab: folder-hierarchy Treeview (left) + text preview (right).

    The tree is built lazily from ``cache.fs_children``.  Only the root
    level is inserted immediately; sub-folders are populated when expanded.

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
        self._cache = None
        # Generation counter – incremented on each load to cancel stale work.
        self._load_gen: int = 0
        self._filter_job: str | None = None
        # Directive-type bitmask per file path (set from cache in load_from_precomputed)
        self._fs_path_flags: dict[str, int] = {}
        # OR of FS_FLAG_* for checked checkboxes; FS_FLAG_ALL = no type filter
        self._type_mask: int = FS_FLAG_ALL
        # Last selected directive and its path (for Extract button)
        self._selected_directive: dict | None = None
        self._selected_path: str = ""
        self._selected_is_folder: bool = False
        self._build()

    def _build(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- left: treeview with scrollbars + filter bar ---
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
        self._tree.bind("<<TreeviewOpen>>", self._on_expand)

        # --- filter bar ---
        filter_bar = ttk.Frame(left)
        filter_bar.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(filter_bar, text="Filter:").pack(side=tk.LEFT)
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", self._on_filter_change)
        self._filter_count_var = tk.StringVar(value="")
        ttk.Label(filter_bar, textvariable=self._filter_count_var).pack(side=tk.RIGHT, padx=(4, 0))
        self._filter_entry = tk.Entry(filter_bar, foreground="gray")
        self._filter_entry.insert(0, _FILTER_PLACEHOLDER)
        self._filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _Tooltip(
            self._filter_entry,
            "^=anchor to start, *=any characters\nMatches file names (including in subfolders)\nExample: ^Begin*Middle",
        )
        ttk.Button(filter_bar, text="×", width=2, command=self._clear_filter).pack(side=tk.LEFT, padx=(2, 0))

        self._ph_active = [True]  # True while the placeholder text is showing

        def _on_focus_in(event) -> None:
            if self._ph_active[0]:
                self._ph_active[0] = False
                self._filter_entry.configure(foreground="black")
                self._filter_entry.delete(0, tk.END)

        def _on_focus_out(event) -> None:
            if not self._filter_entry.get():
                self._ph_active[0] = True
                self._filter_var.set("")
                self._filter_entry.configure(foreground="gray")
                self._filter_entry.delete(0, tk.END)
                self._filter_entry.insert(0, _FILTER_PLACEHOLDER)

        def _on_key_release(event) -> None:
            if not self._ph_active[0]:
                new_val = self._filter_entry.get()
                if self._filter_var.get() != new_val:
                    self._filter_var.set(new_val)

        self._filter_entry.bind("<FocusIn>", _on_focus_in)
        self._filter_entry.bind("<FocusOut>", _on_focus_out)
        self._filter_entry.bind("<KeyRelease>", _on_key_release)

        # --- directive-type checkboxes ---
        cb_bar = ttk.Frame(left)
        cb_bar.pack(fill=tk.X, pady=(2, 0))
        self._fs_show_inline = tk.BooleanVar(value=True)
        self._fs_show_fromarchive = tk.BooleanVar(value=True)
        self._fs_show_patched = tk.BooleanVar(value=True)
        self._fs_show_other = tk.BooleanVar(value=True)
        for _text, _var in (
            ("InlineFile", self._fs_show_inline),
            ("FromArchive", self._fs_show_fromarchive),
            ("PatchedFromArchive", self._fs_show_patched),
            ("Other", self._fs_show_other),
        ):
            ttk.Checkbutton(
                cb_bar, text=_text, variable=_var,
                command=self._on_type_filter_change,
            ).pack(side=tk.LEFT)

        # --- right: preview text area + tools area ---
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        # Tools area pinned to the bottom before preview so it stays visible
        tools_frame = ttk.Frame(right)
        tools_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 0))
        self._extract_btn = ttk.Button(
            tools_frame,
            text="Extract InlineFile",
            state=tk.DISABLED,
            command=self._on_extract_click,
        )
        self._extract_btn.pack(side=tk.LEFT, padx=2, pady=2)

        self._preview = tk.Text(
            right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        sb2 = ttk.Scrollbar(right, command=self._preview.yview)
        self._preview.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self._preview.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------

    def set_loading(self) -> None:
        """Show a 'Loading...' placeholder in the tree while data is fetched."""
        self._tree.delete(*self._tree.get_children())
        self._all_directives = []
        self._cache = None
        self._load_gen += 1
        self._filter_count_var.set("")
        self._selected_directive = None
        self._selected_path = ""
        self._selected_is_folder = False
        self._extract_btn.configure(state=tk.DISABLED)
        self._tree.insert("", tk.END, text="Loading\u2026")

    def load_from_precomputed(
        self, wabba, cache, t0: float | None = None
    ) -> None:
        """Populate the tree root from pre-computed cache data (instant).

        Only root-level entries are inserted now.  Sub-folders get a dummy
        placeholder child; their real children are inserted on first expand.

        *t0* – monotonic timestamp from when the tab was opened, used to
               compute the elapsed-ms figure in the console log line.
        """
        self._wabba = wabba
        self._archives_by_hash = cache.archives_by_hash
        self._all_directives = list(cache.fs_directives or [])
        self._cache = cache
        self._fs_path_flags = dict(cache.fs_path_flags) if cache.fs_path_flags else {}
        self._load_gen += 1

        # Apply any active filter, or show the normal lazy tree
        cur_filter = self._filter_var.get() if not self._ph_active[0] else ""
        if cur_filter or self._type_mask != FS_FLAG_ALL:
            self._apply_filter(cur_filter)
        else:
            self._insert_lazy_root(cache)

        t_insert = time.monotonic()
        n_total = len(cache.fs_sorted_paths or [])
        root_children = (cache.fs_children or {}).get("", [])
        elapsed_ms = int((time.monotonic() - (t0 or t_insert)) * 1000)
        print(
            f"[tab] loaded: Files  "
            f"({len(root_children)} root nodes, {n_total} total nodes, "
            f"{elapsed_ms} ms)"
        )

    def _insert_lazy_root(self, cache) -> None:
        """Insert only root-level nodes (lazy; children expanded on demand)."""
        self._tree.delete(*self._tree.get_children())
        root_children = (cache.fs_children or {}).get("", [])
        fp = cache.fs_folder_paths or set()
        n_files = sum(1 for p in (cache.fs_sorted_paths or []) if p not in fp)
        for path in root_children:
            name = path.rsplit("/", 1)[-1]
            self._tree.insert("", "end", iid=path, text=name)
            if path in fp:
                self._tree.insert(path, "end", iid=_DUMMY_PREFIX + path, text="")
        self._filter_count_var.set(f"{n_files} files")

    # ------------------------------------------------------------------
    # Filter logic
    # ------------------------------------------------------------------

    def _on_filter_change(self, *_) -> None:
        if self._filter_job is not None:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(_FILTER_DEBOUNCE_MS, self._do_filter)

    def _do_filter(self) -> None:
        self._filter_job = None
        text = self._filter_var.get() if not self._ph_active[0] else ""
        self._apply_filter(text)

    def _on_type_filter_change(self) -> None:
        """Recompute the type mask from checkboxes and re-apply the filter."""
        mask = 0
        if self._fs_show_inline.get():
            mask |= FS_FLAG_INLINE
        if self._fs_show_fromarchive.get():
            mask |= FS_FLAG_FROM_ARCHIVE
        if self._fs_show_patched.get():
            mask |= FS_FLAG_PATCHED
        if self._fs_show_other.get():
            mask |= FS_FLAG_OTHER
        self._type_mask = mask
        self._do_filter()

    def _clear_filter(self) -> None:
        """Clear the filter entry and restore the normal lazy tree."""
        self._ph_active[0] = True
        self._filter_var.set("")
        self._filter_entry.configure(foreground="gray")
        self._filter_entry.delete(0, tk.END)
        self._filter_entry.insert(0, _FILTER_PLACEHOLDER)
        self._apply_filter("")

    def _apply_filter(self, text: str) -> None:
        """Rebuild the tree showing only files matching *text* and the type mask.

        Folders that contain no matching descendants are hidden.
        When *text* is empty and all type checkboxes are checked the normal
        lazy tree is restored (fast path).
        """
        cache = self._cache
        if cache is None:
            return

        type_mask = self._type_mask
        type_filter_active = type_mask != FS_FLAG_ALL

        if not text and not type_filter_active:
            self._insert_lazy_root(cache)
            return

        t0 = time.monotonic()
        pattern = _build_name_pattern(text) if text else None

        fp = cache.fs_folder_paths or set()
        sorted_paths = cache.fs_sorted_paths or []
        total_files = sum(1 for p in sorted_paths if p not in fp)

        if text and pattern is None:
            # Invalid filter pattern - show nothing
            self._tree.delete(*self._tree.get_children())
            self._filter_count_var.set("0 files")
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            print(f"[filter:files] '{text}'  {total_files} -> 0  ({elapsed_ms} ms)")
            return

        # Determine which file paths survive both filters, then propagate
        # their ancestor folders into the visible set.
        visible: set[str] = set()
        match_count = 0
        for path in sorted_paths:
            if path in fp:
                continue  # folder – decided by descendants
            # Text filter
            if pattern is not None:
                basename = path.rsplit("/", 1)[-1]
                if pattern.search(basename) is None:
                    continue
            # Type filter
            if type_filter_active:
                if not (self._fs_path_flags.get(path, 0) & type_mask):
                    continue
                # Suppress meta.ini files inside mods/<subfolder>/
                # (noise entries that only clutter the type-filtered view)
                parts_check = path.split("/")
                if (
                    len(parts_check) >= 3
                    and parts_check[0].lower() == "mods"
                    and parts_check[-1].lower() == "meta.ini"
                ):
                    continue
            match_count += 1
            visible.add(path)
            # Mark all ancestor folders visible too
            parts = path.split("/")
            for i in range(1, len(parts)):
                visible.add("/".join(parts[:i]))

        # Rebuild tree with only visible nodes (in sorted order, no lazy load)
        self._tree.delete(*self._tree.get_children())
        for path in sorted_paths:
            if path not in visible:
                continue
            parts = path.split("/")
            parent_iid = "/".join(parts[:-1])
            name = parts[-1]
            try:
                self._tree.insert(parent_iid, "end", iid=path, text=name)
            except tk.TclError:
                pass  # already present

        self._filter_count_var.set(f"{match_count} files")
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        filter_desc = text or "(type filter)"
        print(f"[filter:files] '{filter_desc}'  {total_files} -> {match_count}  ({elapsed_ms} ms)")

    # ------------------------------------------------------------------
    # Lazy expansion
    # ------------------------------------------------------------------

    def _on_expand(self, _event=None) -> None:
        """Insert real children when a folder is opened for the first time."""
        node_id = self._tree.focus()
        if not node_id:
            return
        children = self._tree.get_children(node_id)
        # Only act when the sole child is our dummy placeholder
        if not children or children[0] != _DUMMY_PREFIX + node_id:
            return

        cache = self._cache
        if cache is None:
            return

        # Remove dummy, insert real children
        self._tree.delete(children[0])
        fp = cache.fs_folder_paths or set()
        for path in (cache.fs_children or {}).get(node_id, []):
            name = path.rsplit("/", 1)[-1]
            try:
                self._tree.insert(node_id, "end", iid=path, text=name)
            except tk.TclError:
                pass  # already present (shouldn't happen, but be safe)
            if path in fp:
                dummy = _DUMMY_PREFIX + path
                try:
                    self._tree.insert(path, "end", iid=dummy, text="")
                except tk.TclError:
                    pass

    # ------------------------------------------------------------------
    # Legacy full-load method (kept for compatibility; not called by app.py)
    # ------------------------------------------------------------------

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

        folder_paths: set[str] = set()
        for norm, _ in self._all_directives:
            parts = norm.split("/")
            for i in range(1, len(parts)):
                folder_paths.add("/".join(parts[:i]))

        all_paths: set[str] = set()
        for norm, _ in self._all_directives:
            parts = norm.split("/")
            for i in range(1, len(parts) + 1):
                all_paths.add("/".join(parts[:i]))

        def _sort_key(path: str):
            parts = path.split("/")
            return [
                (0 if "/".join(parts[: i + 1]) in folder_paths else 1, parts[i].lower())
                for i in range(len(parts))
            ]

        for path in sorted(all_paths, key=_sort_key):
            parts = path.split("/")
            name = parts[-1]
            parent_iid = "/".join(parts[:-1])
            self._tree.insert(parent_iid, "end", iid=path, text=name)

    # ------------------------------------------------------------------
    # Selection / preview
    # ------------------------------------------------------------------

    def _on_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        self._show_preview(sel[0])

    def _show_preview(self, path: str) -> None:
        """Build and display the preview for the node at *path*."""
        # Ignore clicks on dummy nodes
        if path.startswith(_DUMMY_PREFIX):
            return
        # Determine whether this node is a folder
        is_folder = bool(
            self._cache is not None
            and path in (self._cache.fs_folder_paths or set())
        )
        # Determine the last directive affecting this path (for Extract button)
        affecting = [
            d for norm, d in self._all_directives
            if norm == path
            or path.startswith(norm + "/")
            or norm.startswith(path + "/")
        ]
        last_d = affecting[-1] if affecting else None
        self._selected_path = path
        self._selected_directive = last_d
        self._selected_is_folder = is_folder
        self._update_extract_btn()

        if is_folder:
            self._preview.configure(state=tk.NORMAL)
            self._preview.delete("1.0", tk.END)
            self._preview.configure(state=tk.DISABLED)
            return

        lines = get_node_preview_lines(
            path, self._all_directives, self._wabba, self._archives_by_hash
        )
        text = "\n".join(lines)
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert(tk.END, text)
        self._preview.configure(state=tk.DISABLED)

    def _update_extract_btn(self) -> None:
        """Enable or disable the Extract button based on current selection."""
        d = self._selected_directive
        if (
            not self._selected_is_folder
            and d is not None
            and self._wabba is not None
            and _get_extract_source_id(d)
        ):
            self._extract_btn.configure(state=tk.NORMAL)
        else:
            self._extract_btn.configure(state=tk.DISABLED)

    def _on_extract_click(self) -> None:
        """Save the inline/patch file from the wabba archive to disk."""
        d = self._selected_directive
        if d is None or self._wabba is None:
            return
        source_id = _get_extract_source_id(d)
        if not source_id:
            return
        to = d.get("To", "")
        default_name = to.replace("\\", "/").rsplit("/", 1)[-1] if to else source_id
        if d.get("$type") == "PatchedFromArchive":
            default_name += ".octodelta"
        _do_extract_inline(self._wabba, source_id, default_name)
