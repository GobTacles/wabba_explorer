"""Problems analysis worker and compare-mode diff-tab population."""

from __future__ import annotations

import time
import tkinter as tk

from ..wabba_file import WabbaFile
from ..wabba.analysis import analyze_directives, AnalysisResult
from ..wabba.cache import WabbaCache


class _BackgroundDiffMixin:
    """Mixin: problems worker, D:Archives diff, D:Directives diff."""

    # ------------------------------------------------------------------
    # Problems analysis worker
    # ------------------------------------------------------------------

    def _run_problems_worker(
        self,
        cache: WabbaCache,
        wabba: WabbaFile | None,
        problems_panel,
    ) -> None:
        """Background thread: run the problems analysis using pre-built caches."""
        cache.prep_done.wait()

        if cache.cancelled:
            return

        _analysis_t0 = time.monotonic()

        def _should_stop() -> bool:
            return cache.cancelled

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
                cache, result, done, wabba,
                list(cache.archives_by_hash.values()),
                problems_panel=problems_panel,
            )

        # Deliver initial 0 % update.
        self.after(
            0,
            lambda: self._update_problems_ui(
                cache,
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
                problems_panel=problems_panel,
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
        cache: WabbaCache,
        result: AnalysisResult,
        done: bool,
        wabba: WabbaFile | None,
        archives: list,
        *,
        problems_panel=None,
    ) -> None:
        self.after(
            0,
            lambda: self._update_problems_ui(
                cache,
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
                low_usage_archives=result.low_usage_archives if done else None,
                problems_panel=problems_panel,
            ),
        )

    def _update_problems_ui(
        self,
        cache: WabbaCache,
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
        low_usage_archives: list | None = None,
        problems_panel=None,
    ) -> None:
        if cache.cancelled:
            return
        panel = problems_panel if problems_panel is not None else self._problems_panel
        panel.update_analysis(
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
            low_usage_archives=low_usage_archives,
        )

    # ------------------------------------------------------------------
    # D:Archives diff tab (compare mode)
    # ------------------------------------------------------------------

    def _try_populate_diff_archives(self) -> None:
        """Populate the D:Archives panel from the pre-computed DiffCache.

        If the background diff task has not finished yet, polls every 150 ms
        until it is ready or the tab is no longer visible.
        """
        panel = getattr(self, "_diff_archives_panel", None)
        if panel is None:
            return

        diff_cache = getattr(self, "_diff_cache", None)
        if diff_cache is None:
            return

        if diff_cache.cancelled:
            return

        if diff_cache.diff_archives_ready.is_set():
            items = diff_cache.diff_archives_items
            panel.load_items(items)
            updated = sum(1 for i in items if i.get("_diff_side") == "updated")
            removed = sum(1 for i in items if i.get("_diff_side") == "removed")
            added = sum(1 for i in items if i.get("_diff_side") == "added")
            multipart = sum(1 for i in items if i.get("_diff_side") in ("removed-multipart", "added-multipart"))
            multipart_str = f", {multipart} multipart" if multipart else ""
            print(
                f"[tab] loaded: D:Archives  "
                f"({updated} updated, {removed} removed, {added} added{multipart_str})"
            )
        else:
            def _retry() -> None:
                try:
                    current = self._main_nb.tab(self._main_nb.select(), "text")
                except tk.TclError:
                    return
                if current == "D:Archives":
                    self._try_populate_diff_archives()

            self.after(150, _retry)

    # ------------------------------------------------------------------
    # D:Directives diff tab (compare mode)
    # ------------------------------------------------------------------

    def _try_populate_diff_directives(self) -> None:
        """Populate the D:Directives panel from the pre-computed DiffCache.

        If the background diff task has not finished yet, polls every 150 ms
        until it is ready or the tab is no longer visible.
        """
        panel = getattr(self, "_diff_directives_panel", None)
        if panel is None:
            return

        diff_cache = getattr(self, "_diff_cache", None)
        if diff_cache is None:
            return

        if diff_cache.cancelled:
            return

        if diff_cache.diff_directives_ready.is_set():
            items = diff_cache.diff_directives_items
            panel.load_items(items)
            a_only = sum(1 for i in items if i.get("_diff_side") == "A only")
            b_only = sum(1 for i in items if i.get("_diff_side") == "B only")
            changed = sum(1 for i in items if i.get("_diff_side") == "changed") // 2
            print(
                f"[tab] loaded: D:Directives  "
                f"({a_only} A-only, {b_only} B-only, {changed} changed pairs, "
                f"{len(items)} total rows)"
            )
        else:
            def _retry() -> None:
                try:
                    current = self._main_nb.tab(self._main_nb.select(), "text")
                except tk.TclError:
                    return
                if current == "D:Directives":
                    self._try_populate_diff_directives()

            self.after(150, _retry)
