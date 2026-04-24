"""Main application window and ``run_gui`` entry point (tkinter)."""

import time
import tkinter as tk
from tkinter import ttk

from .. import __version__
from ..wabba_file import WabbaFile
from ..wabba.cache import WabbaCache, DiffCache
from .stdout_redirect import _StdoutRedirect
from .gui_menu import _MenuMixin
from .gui_about import _AboutMixin
from .gui_background import _BackgroundMixin
from .gui_tab_modlist_json import _TabModlistJson
from .gui_tab_archives import _TabArchives
from .gui_tab_files import _TabFiles
from .gui_tab_directives import _TabDirectives
from .gui_tab_edit_changes import _TabEditChanges
from .gui_tab_problems import _TabProblems
from .gui_util import _archive_label


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class WabbaExplorerApp(
    _MenuMixin,
    _AboutMixin,
    _BackgroundMixin,
    _TabModlistJson,
    _TabArchives,
    _TabFiles,
    _TabDirectives,
    _TabEditChanges,
    _TabProblems,
    tk.Tk,
):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"Wabba Explorer {__version__}")
        self.geometry("1100x750")
        self.minsize(700, 500)

        self._wabba: WabbaFile | None = None
        self._orig_stdout = __import__("sys").stdout
        self._orig_stderr = __import__("sys").stderr
        self._outer_paned: ttk.PanedWindow | None = None

        # Tab dispatch: tab_text → info dict (type, wabba, panel, …).
        # Each entry is populated by the tab builder.
        self._tab_dispatch: dict[str, dict] = {}

        # Previous tab text, used for filter-sync on tab switch.
        self._prev_tab_text: str = ""

        # Records when each tab was last opened (for load-time ms reporting).
        self._tab_open_times: dict[str, float] = {}

        self._recent_files: list[str] = []
        self._max_recent_files = 8
        self._recent_files_menu: tk.Menu | None = None
        self._compare_mode: bool = False
        self._compare_paths: dict[str, str] = {}
        self._last_compare_a: str = ""
        self._last_compare_b: str = ""
        self._last_mode: str = "normal"

        # The D:Archives filtered list panel (compare mode only).
        self._diff_archives_panel = None
        # The D:Directives filtered list panel (compare mode only).
        self._diff_directives_panel = None
        # Pre-computed diff results for the current compare session.
        self._diff_cache: DiffCache | None = None

        # Single-file mode compat attrs (set by tab builders when wabba=None).
        self._archives_panel = None
        self._files_panel = None
        self._directives_panel = None
        self._problems_panel = None
        self._key_listbox = None
        self._content_label = None
        self._content_text = None

        self._load_recent_files()
        self._init_edit_queue_state()
        self._build_ui()
        self._build_menubar()
        self._redirect_output_streams()
        self.after(100, self._init_sash)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._status_var = tk.StringVar(value="Ready – open a .wabbajack file.")
        ttk.Label(
            self, textvariable=self._status_var, anchor=tk.W, relief=tk.SUNKEN
        ).pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=2)

        outer = ttk.PanedWindow(self, orient=tk.VERTICAL)
        outer.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._outer_paned = outer

        self._main_nb = ttk.Notebook(outer)
        outer.add(self._main_nb, weight=4)

        self._rebuild_main_tabs(compare_mode=False)
        self._main_nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

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

    def _redirect_output_streams(self) -> None:
        import sys
        sys.stdout = _StdoutRedirect(self._console_text, self._orig_stdout)
        sys.stderr = _StdoutRedirect(self._console_text, self._orig_stderr)

    # ------------------------------------------------------------------
    # Sash initialisation
    # ------------------------------------------------------------------

    def _init_sash(self) -> None:
        """Place the sash so the console takes ~20 % of the paned area."""
        if self._outer_paned is None:
            return
        total = self._outer_paned.winfo_height()
        if total > 1:
            self._outer_paned.sashpos(0, int(total * 0.80))

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
    # Tab building
    # ------------------------------------------------------------------

    def _rebuild_main_tabs(
        self, *, compare_mode: bool, compare_paths: dict[str, str] | None = None
    ) -> None:
        """Destroy all tabs and rebuild them, optionally in compare mode.

        In compare mode:
        - Both WabbaFiles are opened immediately.
        - Background pipelines for both start simultaneously.
        - Each tab is bound directly to its WabbaFile (no ref-swapping later).
        """
        from tkinter import messagebox

        for tab_id in self._main_nb.tabs():
            self._main_nb.forget(tab_id)
        self._tab_open_times.clear()
        self._tab_dispatch.clear()
        self._compare_mode = compare_mode
        self._compare_paths = compare_paths or {}
        self._diff_archives_panel = None
        self._diff_directives_panel = None
        self._prev_tab_text = ""

        # Cancel the old diff cache if one was running.
        if self._diff_cache is not None:
            self._diff_cache.cancelled = True
            self._diff_cache = None

        # Cancel and close any compare WabbaFiles still running (from previous
        # compare session).  We find them by scanning tab_dispatch of the
        # *old* session – but since we already cleared it, we instead use the
        # wabba stored on self._wabba if in compare mode.
        # Simpler: cancel_all_wabbas is called in destroy(); here just cancel
        # any compare wabbas we can find.
        for old_w in getattr(self, "_compare_wabbas_list", []):
            if old_w is not None and old_w is not self._wabba:
                if old_w.cache:
                    old_w.cache.cancelled = True
                old_w.close()
        self._compare_wabbas_list: list[WabbaFile] = []

        if not compare_mode:
            self._build_tab_modlist_json("main")
            self._build_tab_file_explorer("Files")
            self._build_tab_archives("Archives")
            self._build_tab_directives("Directives")
            self._build_tab_problems("Problems")
            self._build_tab_edit_changes("Edit/Changes")
            return

        # ── Compare mode: interleaved A/B pairs ──────────────────────────
        # Tab order:
        #   A:main  B:main  A:Files  B:Files
        #   D:Archives  A:Archives  B:Archives
        #   D:Directives  A:Directives  B:Directives
        #   A:Problems  B:Problems
        _cmp_t0 = time.monotonic()
        print("[wabba_explorer] starting compare mode")
        paths = compare_paths or {}
        path_a = paths.get("A", "")
        path_b = paths.get("B", "")

        # Open both WabbaFiles now so background tasks start simultaneously.
        def _open(label: str, path: str) -> "WabbaFile | None":
            if not path:
                return None
            _t = time.monotonic()
            print(f"[wabba_explorer] [{label}] opening archive: {path}")
            wabba = WabbaFile(path)
            try:
                wabba.open()
            except Exception as exc:
                messagebox.showerror("Error", f"Could not open file:\n{exc}")
                return None
            wabba.cache = WabbaCache()
            print(f"[wabba_explorer] [{label}] archive opened  ({int((time.monotonic()-_t)*1000)} ms)")
            return wabba

        wabba_a = _open("A", path_a)
        wabba_b = _open("B", path_b)

        if wabba_a is None or wabba_b is None:
            # Fall back to single-file mode if either open failed.
            self._compare_mode = False
            self._build_tab_modlist_json("main")
            self._build_tab_file_explorer("Files")
            self._build_tab_archives("Archives")
            self._build_tab_directives("Directives")
            self._build_tab_problems("Problems")
            return

        self._compare_wabbas_list = [wabba_a, wabba_b]

        # Build tabs in pairs.
        _t = time.monotonic()
        print("[wabba_explorer] building compare tabs…")
        for p, w in (("A", wabba_a), ("B", wabba_b)):
            self._build_tab_modlist_json(f"{p}:main", wabba=w)
        for p, w in (("A", wabba_a), ("B", wabba_b)):
            self._build_tab_file_explorer(f"{p}:Files", wabba=w)

        self._build_tab_diff_archives()
        for p, w in (("A", wabba_a), ("B", wabba_b)):
            self._build_tab_archives(f"{p}:Archives", wabba=w)

        self._build_tab_diff_directives(wabba_a, wabba_b)
        for p, w in (("A", wabba_a), ("B", wabba_b)):
            self._build_tab_directives(f"{p}:Directives", wabba=w)

        for p, w in (("A", wabba_a), ("B", wabba_b)):
            self._build_tab_problems(f"{p}:Problems", wabba=w)
        print(f"[wabba_explorer] compare tabs built  ({int((time.monotonic()-_t)*1000)} ms)")

        # Start both background pipelines simultaneously.
        print("[wabba_explorer] starting background pipelines…")
        for prefix, wabba in (("A:", wabba_a), ("B:", wabba_b)):
            prob_info = self._tab_dispatch.get(f"{prefix}Problems")
            problems_panel = prob_info["panel"] if prob_info else None
            self._remember_recent_file(wabba.path)
            self._start_loading_wabba(
                wabba,
                tab_prefix=prefix,
                problems_panel=problems_panel,
            )

        # Create a fresh DiffCache and launch the background diff workers.
        # They wait internally on cache_a/cache_b ready events before computing.
        import threading as _threading
        from ..wabba.loader import run_diff_archives_prep, run_diff_directives_prep
        diff_cache = DiffCache()
        self._diff_cache = diff_cache
        cache_a = wabba_a.cache
        cache_b = wabba_b.cache
        _threading.Thread(
            target=run_diff_archives_prep,
            args=(cache_a, cache_b, diff_cache),
            daemon=True,
        ).start()
        _threading.Thread(
            target=run_diff_directives_prep,
            args=(cache_a, cache_b, diff_cache),
            daemon=True,
        ).start()

        self._remember_compare_files(path_a, path_b)
        print(f"[wabba_explorer] compare mode ready  ({int((time.monotonic()-_cmp_t0)*1000)} ms total)")

    # ------------------------------------------------------------------
    # D:Archives tab (compare mode)
    # ------------------------------------------------------------------

    def _build_tab_diff_archives(self) -> None:
        """Build the 'D:Archives' tab that shows archives differing between A and B."""
        import json as _json
        from .filtered_list_panel import _FilteredListPanel
        from .gui_util import _archive_item_matches

        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="D:Archives")

        def _diff_label(item: dict) -> str:
            side = item.get("_diff_side", "?")
            if side == "updated":
                a_name = item.get("Name", "") or ""
                b_arch = item.get("_b_archive") or {}
                b_name = b_arch.get("Name", "") or ""
                # Find common prefix
                prefix_len = 0
                for i in range(min(len(a_name), len(b_name))):
                    if a_name[i] == b_name[i]:
                        prefix_len += 1
                    else:
                        break
                prefix = a_name[:prefix_len]
                a_rest = a_name[prefix_len:]
                b_rest = b_name[prefix_len:]
                # Find common suffix (not overlapping the prefix remainder)
                suffix_len = 0
                for i in range(1, min(len(a_rest), len(b_rest)) + 1):
                    if a_rest[-i] == b_rest[-i]:
                        suffix_len += 1
                    else:
                        break
                suffix = a_rest[-suffix_len:] if suffix_len else ""
                midfix_a = a_rest[: len(a_rest) - suffix_len] if suffix_len else a_rest
                midfix_b = b_rest[: len(b_rest) - suffix_len] if suffix_len else b_rest
                return f"[updated] {prefix}[[ {midfix_a} -> {midfix_b} ]]{suffix}"
            return f"[{side}] {_archive_label(item)}"

        def _diff_extra_info(item: dict) -> str:
            side = item.get("_diff_side", "")
            if side == "updated":
                b_arch = item.get("_b_archive")
                # Show a clean view: strip internal diff keys from both copies.
                _skip = {"_diff_side", "_diff_wabba", "_ver_a", "_ver_b", "_b_archive"}
                a_clean = {k: v for k, v in item.items() if k not in _skip}
                lines = [
                    "=== A version ===",
                    _json.dumps(a_clean, indent=2),
                ]
                if isinstance(b_arch, dict):
                    lines.append("\n=== B version ===")
                    lines.append(_json.dumps(b_arch, indent=2))
                return "\n".join(lines)
            return ""

        self._diff_archives_panel = _FilteredListPanel(
            frame,
            label_fn=_diff_label,
            filter_fn=lambda item, t, pat: _archive_item_matches(item, t, pat),
            extra_info_fn=_diff_extra_info,
        )
        self._diff_archives_panel.pack(fill=tk.BOTH, expand=True)

        self._tab_dispatch["D:Archives"] = {
            "type": "D:Archives",
            "wabba": None,
            "panel": self._diff_archives_panel,
        }

    # ------------------------------------------------------------------
    # D:Directives tab (compare mode)
    # ------------------------------------------------------------------

    def _build_tab_diff_directives(self, wabba_a, wabba_b) -> None:
        """Build the 'D:Directives' tab showing directives that differ between A and B.

        Differences are computed by :func:`wabba.diff.diff_directives`.
        ``SourceDataID`` / ``PatchID`` UUID values are resolved to
        ``(CRC32, size)`` signatures so that identical inline files stored
        under different UUIDs are not falsely reported as different.
        """
        from .filtered_list_panel import _FilteredListPanel
        from .gui_util import _item_matches, _directive_label
        from ..wabba.entry_info import get_directive_detail_text

        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="D:Directives")

        def _diff_dir_label(item: dict) -> str:
            side = item.get("_diff_side", "?")
            base = _directive_label(item)
            if side == "changed":
                wabba_side = item.get("_diff_wabba", "?")
                return f"[{wabba_side}:changed] {base}"
            return f"[{side}] {base}"

        def _diff_dir_extra_info(item: dict) -> str:
            wabba_side = item.get("_diff_wabba", "")
            key = f"{wabba_side}:Directives" if wabba_side else None
            info = self._tab_dispatch.get(key) if key else None
            w = info.get("wabba") if info else None
            cache = w.cache if w else None
            archives_by_hash = cache.archives_by_hash if cache else {}
            return get_directive_detail_text(item, archives_by_hash, w)

        self._diff_directives_panel = _FilteredListPanel(
            frame,
            label_fn=_diff_dir_label,
            filter_fn=lambda item, t, pat: _item_matches(item, t, pat, "To", "Hash"),
            extra_info_fn=_diff_dir_extra_info,
        )
        self._diff_directives_panel.pack(fill=tk.BOTH, expand=True)

        self._tab_dispatch["D:Directives"] = {
            "type": "D:Directives",
            "wabba": None,
            "wabba_a": wabba_a,
            "wabba_b": wabba_b,
            "panel": self._diff_directives_panel,
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def destroy(self) -> None:
        import sys
        if isinstance(sys.stdout, _StdoutRedirect):
            sys.stdout = self._orig_stdout
        if isinstance(sys.stderr, _StdoutRedirect):
            sys.stderr = self._orig_stderr
        if self._wabba is not None:
            if self._wabba.cache is not None:
                self._wabba.cache.cancelled = True
            self._wabba.close()
        for cw in getattr(self, "_compare_wabbas_list", []):
            if cw is not None and cw is not self._wabba:
                if cw.cache is not None:
                    cw.cache.cancelled = True
                cw.close()
        if self._diff_cache is not None:
            self._diff_cache.cancelled = True
            self._diff_cache = None
        super().destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_gui(initial_file: str | None = None, *, auto_open_recent: bool = False) -> None:
    """Launch the GUI.  Optionally open *initial_file* on start-up.

    When *auto_open_recent* is True and no *initial_file* is given:
    - If the last session was compare mode and both compare paths are
      remembered, compare mode is started automatically with those paths.
    - Otherwise the most recent file from the persistent recent-files list
      is opened automatically in single-file mode.
    """
    print(f"wabba_explorer {__version__}")
    app = WabbaExplorerApp()
    if initial_file:
        app._load_file(initial_file)
    elif auto_open_recent:
        if (
            app._last_mode == "compare"
            and app._last_compare_a
            and app._last_compare_b
        ):
            app._rebuild_main_tabs(
                compare_mode=True,
                compare_paths={"A": app._last_compare_a, "B": app._last_compare_b},
            )
        elif app._recent_files:
            app._load_file(app._recent_files[0])
    app.mainloop()
