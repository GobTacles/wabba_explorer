"""File-loading and background-pipeline methods for WabbaExplorerApp."""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

from .. import __version__
from ..wabba_file import WabbaFile
from ..wabba.cache import WabbaCache
from ..wabba.loader import parse_modlist, run_prep, run_archives_prep, run_directives_prep, run_files_prep
from .gui_util import _key_label
from .gui_util import _WABBA_FILE_KEY, _build_wabba_file_preview


class _BackgroundLoaderMixin:
    """Mixin: file open/load and background-pipeline management."""

    # ------------------------------------------------------------------
    # File loading
    # ------------------------------------------------------------------

    def _on_open(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="Open .wabbajack file",
            filetypes=[("Wabbajack archives", "*.wabbajack"), ("All files", "*.*")],
        )
        if not path:
            return
        if getattr(self, "_compare_mode", False):
            self._rebuild_main_tabs(compare_mode=False)
        self._load_file(path)

    def _load_file(self, path: str) -> None:
        """Open *path* in single-file mode, cancelling any previous load."""
        # Cancel the previous wabba so its workers stop as soon as possible.
        if self._wabba is not None:
            if self._wabba.cache is not None:
                self._wabba.cache.cancelled = True
            self._wabba.close()
            self._wabba = None

        wabba = WabbaFile(path)
        try:
            wabba.open()
        except Exception as exc:
            messagebox.showerror("Error", f"Could not open file:\n{exc}")
            return

        cache = WabbaCache()
        wabba.cache = cache
        self._wabba = wabba

        self._remember_recent_file(path)
        self._remember_normal_mode()

        # Determine the Problems panel from tab_dispatch (single-file mode).
        prob_info = self._tab_dispatch.get("Problems")
        problems_panel = prob_info["panel"] if prob_info else self._problems_panel

        self._start_loading_wabba(wabba, tab_prefix="", problems_panel=problems_panel)

    def _start_loading_wabba(
        self,
        wabba: WabbaFile,
        *,
        tab_prefix: str,
        problems_panel,
    ) -> None:
        """Show loading placeholders and start the background pipeline.

        *tab_prefix* selects the tab-dispatch entries to update.  Use ``""``
        for single-file mode and ``"A:"`` / ``"B:"`` for compare mode.
        """
        cache = wabba.cache
        path = wabba.path

        # Resolve per-tab panel references from _tab_dispatch.
        def _get(suffix):
            return self._tab_dispatch.get(f"{tab_prefix}{suffix}")

        main_info = _get("main")
        archives_info = _get("Archives")
        files_info = _get("Files")
        directives_info = _get("Directives")

        # Show loading placeholders immediately.
        if main_info:
            main_info["key_listbox"].delete(0, tk.END)
            main_info["key_listbox"].insert(tk.END, "Loading…")
        if archives_info:
            archives_info["panel"].set_loading()
        if files_info:
            files_info["panel"].set_loading()
        if directives_info:
            directives_info["panel"].set_loading()

        _report_header = "\n".join([
            f"Wabba Explorer {__version__} problem report for {os.path.basename(path)}",
            f"Path: {path}",
        ])
        if problems_panel is not None:
            problems_panel.set_analyzing(header=_report_header)

        self._status_var.set(f"Loading: {path} …")
        self.update()

        self.after(
            100,
            lambda: threading.Thread(
                target=self._file_loader_thread,
                args=(wabba, cache, main_info, problems_panel, tab_prefix),
                daemon=True,
            ).start(),
        )

    def _file_loader_thread(
        self,
        wabba: WabbaFile,
        cache: WabbaCache,
        main_info: "dict | None",
        problems_panel,
        tab_prefix: str = "",
    ) -> None:
        """Background thread: parse JSON → prep → launch per-tab workers."""
        # Derive short label ("A", "B", or "") from tab_prefix ("A:", "B:", "").
        _label = tab_prefix.rstrip(":") if tab_prefix else ""

        # ── Phase 1: parse modlist JSON ──────────────────────────────────
        parse_modlist(wabba, cache)

        # Notify UI so the main/modlist tab can be populated without waiting for prep.
        self.after(0, self._on_modlist_parsed, wabba, cache, main_info, problems_panel, tab_prefix)

        # ── Phase 2: common prep (build shared lookup caches) ────────────
        if cache.cancelled:
            return
        run_prep(wabba, cache, label=_label)

        if cache.cancelled:
            return

        _side = f"[{_label}] " if _label else ""
        print(f"[wabba_explorer] {_side}Archives: {len(cache.archives)} entries")
        print(f"[wabba_explorer] {_side}Directives: {len(cache.directives)} entries")

        # ── Phase 3: per-tab prep (all start in parallel) ────────────────
        threading.Thread(
            target=run_archives_prep, args=(cache,), kwargs={"label": _label}, daemon=True
        ).start()
        threading.Thread(
            target=run_directives_prep, args=(cache,), kwargs={"label": _label}, daemon=True
        ).start()
        threading.Thread(
            target=run_files_prep, args=(cache,), kwargs={"label": _label}, daemon=True
        ).start()
        threading.Thread(
            target=self._run_problems_worker,
            args=(cache, wabba, problems_panel),
            daemon=True,
        ).start()

        # Notify UI that background workers are running so tabs can start polling.
        self.after(0, self._on_tab_changed)

    def _on_modlist_parsed(
        self,
        wabba: WabbaFile,
        cache: WabbaCache,
        main_info: "dict | None",
        problems_panel,
        tab_prefix: str = "",
    ) -> None:
        """Main-thread callback: populate the main/modlist tab immediately."""
        if cache.cancelled:
            return

        modlist_data = cache.modlist_data
        base_keys = list(modlist_data.keys()) if modlist_data else []
        modlist_keys = [_WABBA_FILE_KEY, *base_keys]

        path = wabba.path
        try:
            st = os.stat(path)
            name = str((modlist_data or {}).get("Name", "") or "")
            version_name = str((modlist_data or {}).get("Version", "") or "")
            wabba_file_preview = _build_wabba_file_preview(
                path=path,
                file_size=st.st_size,
                modified_ts=st.st_mtime,
                name=name,
                version=version_name,
            )
        except OSError:
            wabba_file_preview = _build_wabba_file_preview(
                path=path,
                file_size=0,
                modified_ts=0.0,
                name=str((modlist_data or {}).get("Name", "") or ""),
                version=str((modlist_data or {}).get("Version", "") or ""),
            )

        # Store parsed data into the tab_state so the listbox select closure
        # can find it without going through self.*.
        if main_info is not None:
            tab_state = main_info.get("tab_state", {})
            tab_state["modlist_data"] = modlist_data
            tab_state["modlist_keys"] = modlist_keys
            tab_state["wabba_file_preview"] = wabba_file_preview

            key_listbox = main_info["key_listbox"]
            key_listbox.delete(0, tk.END)
            for key in modlist_keys:
                if key == _WABBA_FILE_KEY:
                    key_listbox.insert(tk.END, _key_label(key, wabba_file_preview))
                elif modlist_data and key in modlist_data:
                    key_listbox.insert(tk.END, _key_label(key, modlist_data[key]))

            set_content = main_info.get("set_content")
            if set_content:
                set_content("Select a key on the left to preview its contents.")
            content_label = main_info.get("content_label")
            if content_label:
                content_label.configure(text="Preview")

        # Append modlist version to Problems header.
        version = (modlist_data.get("Version", "") if modlist_data else "") or "unknown"
        if problems_panel is not None:
            problems_panel.add_problem_report_line(f"Version: {version}")

        n = len(modlist_keys)
        self._status_var.set(f"Opened: {path}  ({n} modlist keys)")
        # In compare mode include the side label (e.g. "A") so the two files
        # are easy to tell apart in the console output.
        side_prefix = f"[{tab_prefix.rstrip(':')}] " if tab_prefix else ""
        print(f"[wabba_explorer] {side_prefix}Opened '{path}'")

        # If the currently visible tab is a heavy tab for this wabba, start polling.
        self._on_tab_changed()
