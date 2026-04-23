"""Background-task methods for WabbaExplorerApp."""

from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

from .. import __version__
from ..wabba_file import WabbaFile
from ..wabba.analysis import analyze_directives, AnalysisResult
from ..wabba.cache import WabbaCache
from ..wabba.loader import parse_modlist, run_prep, run_archives_prep, run_directives_prep, run_files_prep
from .gui_util import _key_label


class _BackgroundMixin:
    """Mixin that provides file-loading and background-worker methods."""

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
        self._load_file(path)

    def _load_file(self, path: str) -> None:
        # Cancel any background workers for the previous file
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

        self._load_id += 1
        load_id = self._load_id

        # Create and attach a fresh cache to the WabbaFile object
        cache = WabbaCache()
        wabba.cache = cache

        self._wabba = wabba
        self._remember_recent_file(path)
        self._modlist_data = None
        self._modlist_keys = []

        # Show loading placeholders immediately
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
        self.update()

        # Launch the background loading pipeline
        self.after(
            100,
            lambda: threading.Thread(
                target=self._file_loader_thread,
                args=(path, wabba, cache, load_id),
                daemon=True,
            ).start(),
        )

    def _file_loader_thread(
        self,
        path: str,
        wabba: WabbaFile,
        cache: WabbaCache,
        load_id: int,
    ) -> None:
        """Background thread: parse JSON → prep → launch per-tab workers."""
        # ── Phase 1: parse modlist JSON ──────────────────────────────────
        parse_modlist(wabba, cache)

        # Notify UI so modlist_json tab can be populated without waiting for prep
        self.after(0, self._on_modlist_parsed, path, wabba, cache, load_id)

        # ── Phase 2: common prep (build shared lookup caches) ────────────
        if cache.cancelled or load_id != self._load_id:
            return
        run_prep(wabba, cache)

        if cache.cancelled or load_id != self._load_id:
            return

        print(f"[wabba_explorer] Archives: {len(cache.archives)} entries")
        print(f"[wabba_explorer] Directives: {len(cache.directives)} entries")

        # ── Phase 3: per-tab prep (all start in parallel) ────────────────
        threading.Thread(
            target=run_archives_prep, args=(cache,), daemon=True
        ).start()
        threading.Thread(
            target=run_directives_prep, args=(cache,), daemon=True
        ).start()
        threading.Thread(
            target=run_files_prep, args=(cache,), daemon=True
        ).start()
        threading.Thread(
            target=self._run_problems_worker,
            args=(cache, wabba, load_id),
            daemon=True,
        ).start()

        # Notify UI that background workers are running so tabs can start polling
        self.after(0, self._on_tab_changed)

    def _on_modlist_parsed(
        self,
        path: str,
        wabba: WabbaFile,
        cache: WabbaCache,
        load_id: int,
    ) -> None:
        """Main-thread callback: populate the modlist-json tab immediately."""
        if load_id != self._load_id:
            return  # stale callback from a superseded load

        self._modlist_data = cache.modlist_data
        self._modlist_keys = list(cache.modlist_data.keys()) if cache.modlist_data else []

        # Append modlist version to Problems header
        version = (cache.modlist_data.get("Version", "") if cache.modlist_data else "") or "unknown"
        self._problems_panel.add_problem_report_line(f"Version: {version}")

        # Populate modlist key listbox (very fast)
        self._key_listbox.delete(0, tk.END)
        if self._modlist_data:
            for key in self._modlist_keys:
                self._key_listbox.insert(tk.END, _key_label(key, self._modlist_data[key]))

        n = len(self._modlist_keys)
        self._status_var.set(f"Opened: {path}  ({n} modlist keys)")
        print(f"[wabba_explorer] Opened '{path}' – {n} modlist top-level keys")

        self._set_content("Select a key on the left to preview its contents.")
        self._content_label.configure(text="Preview")

        # If we are already on a heavy tab, start polling it
        self._on_tab_changed()

    # ------------------------------------------------------------------
    # Tab-change and polling
    # ------------------------------------------------------------------

    def _on_tab_changed(self, _event=None) -> None:
        """Populate the current tab if its background data is ready, or poll."""
        try:
            tab_text = self._main_nb.tab(self._main_nb.select(), "text")
        except tk.TclError:
            return

        if _event is not None:
            self._tab_open_times[tab_text] = time.monotonic()
            print(f"[tab] opened: {tab_text}")

        cache = self._wabba.cache if self._wabba else None
        if cache is None:
            return

        if tab_text == "Archives":
            if cache.archives_ready.is_set():
                self._load_archives_tab(cache)
            else:
                self._poll_tab_ready(
                    "Archives",
                    cache,
                    cache.archives_ready,
                    self._load_archives_tab,
                )

        elif tab_text == "Directives":
            if cache.directives_ready.is_set():
                self._populate_directives_tab(cache)
            else:
                self._poll_tab_ready(
                    "Directives",
                    cache,
                    cache.directives_ready,
                    self._populate_directives_tab,
                )

        elif tab_text == "Files":
            if cache.files_ready.is_set():
                t0 = self._tab_open_times.get("Files")
                self._files_panel.load_from_precomputed(self._wabba, cache, t0=t0)
            else:
                self._poll_tab_ready(
                    "Files",
                    cache,
                    cache.files_ready,
                    lambda c: self._files_panel.load_from_precomputed(
                        self._wabba, c, t0=self._tab_open_times.get("Files")
                    ),
                )
        # Problems tab: analysis runs automatically in background;
        # _ProblemsPanel accumulates lines via add_problem_report_line.

    def _poll_tab_ready(
        self,
        tab_name: str,
        cache: WabbaCache,
        event: "threading.Event",
        callback,
        delay_ms: int = 150,
    ) -> None:
        """Poll *event* every *delay_ms* ms; call *callback(cache)* when set.

        Stops polling if the cache is cancelled or the user switches away
        from the tab.
        """
        def _check() -> None:
            if cache.cancelled:
                return
            if event.is_set():
                # Only populate if still on this tab
                try:
                    current = self._main_nb.tab(self._main_nb.select(), "text")
                except tk.TclError:
                    return
                if current == tab_name:
                    callback(cache)
            else:
                self.after(delay_ms, _check)

        self.after(delay_ms, _check)

    def _load_archives_tab(self, cache: WabbaCache) -> None:
        """Populate the Archives panel from the pre-built model."""
        t0 = self._tab_open_times.get("Archives")
        if cache.archive_model is not None:
            self._archives_panel.load_model(cache.archive_model)
        else:
            self._archives_panel.load_items(cache.archives)
        self.update_archive_filter_counts(cache.archives)
        elapsed = int((time.monotonic() - (t0 or time.monotonic())) * 1000)
        print(f"[tab] loaded: Archives  ({len(cache.archives)} entries, {elapsed} ms)")

    def _populate_directives_tab(self, cache: WabbaCache) -> None:
        """Populate the Directives panel and print type counts to console."""
        if cache.directive_type_counts is not None:
            print("[wabba_explorer] Directives $type counts:")
            for dtype, count in sorted(
                cache.directive_type_counts.items(), key=lambda x: -x[1]
            ):
                print(f"  {dtype}: {count}")
        t0 = self._tab_open_times.get("Directives")
        if cache.directive_model is not None:
            self._directives_panel.load_model(cache.directive_model)
        else:
            self._directives_panel.load_items(cache.directives)
        elapsed = int((time.monotonic() - (t0 or time.monotonic())) * 1000)
        print(f"[tab] loaded: Directives  ({len(cache.directives)} entries, {elapsed} ms)")

    # ------------------------------------------------------------------
    # Problems analysis worker
    # ------------------------------------------------------------------

    def _run_problems_worker(
        self,
        cache: WabbaCache,
        wabba: WabbaFile | None,
        load_id: int,
    ) -> None:
        """Background thread: run the problems analysis using pre-built caches."""
        # Wait for prep; this is almost instant because the caller already
        # ran run_prep() before spawning this thread, but the event ensures
        # correctness if ever called early.
        cache.prep_done.wait()

        if cache.cancelled or load_id != self._load_id:
            return

        _analysis_t0 = time.monotonic()

        def _should_stop() -> bool:
            return cache.cancelled or load_id != self._load_id

        def _on_progress(result: AnalysisResult, done: bool) -> None:
            cache.analysis_progress = result
            if done:
                cache.analysis_result = result
                cache.analysis_done = True
                elapsed_ms = int((time.monotonic() - _analysis_t0) * 1000)
                print(
                    f"[bg] problems analysis done  "
                    f"({result.total} directives, {result.mismatches} mismatches, {elapsed_ms} ms)"
                )
            self._schedule_problems_update(
                load_id,
                result,
                done,
                wabba,
                list(cache.archives_by_hash.values()),
            )

        # Deliver initial 0 % update
        self.after(
            0,
            lambda: self._update_problems_ui(
                load_id,
                total=len(cache.directives),
                processed=0,
                matches=0,
                mismatches=0,
                ignores=0,
                elapsed=0.0,
                mismatch_directives=[],
                wabba=wabba,
                archives=list(cache.archives_by_hash.values()),
                done=False,
            ),
        )

        analyze_directives(
            cache.directives,
            list(cache.archives_by_hash.values()),
            wabba,
            should_stop=_should_stop,
            on_progress=_on_progress,
            precomputed_archive_hashes=set(cache.archives_by_hash),
            precomputed_root_names=cache.wabba_root_names,
        )

    def _schedule_problems_update(
        self,
        load_id: int,
        result: AnalysisResult,
        done: bool,
        wabba: WabbaFile | None,
        archives: list,
    ) -> None:
        self.after(
            0,
            lambda: self._update_problems_ui(
                load_id,
                total=result.total,
                processed=result.processed,
                matches=result.matches,
                mismatches=result.mismatches,
                ignores=result.ignores,
                elapsed=result.elapsed,
                mismatch_directives=result.mismatch_directives,
                wabba=wabba,
                archives=archives,
                done=done,
                unused_archives=result.unused_archives if done else None,
                missing_archives=result.missing_archives if done else None,
                missing_inline_files=result.missing_inline_files if done else None,
                unused_inline_files=result.unused_inline_files if done else None,
            ),
        )

    def _update_problems_ui(
        self,
        load_id: int,
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
        unused_inline_files: list[str] | None = None,
    ) -> None:
        if load_id != self._load_id:
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
            unused_inline_files=unused_inline_files,
        )
