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
        """Populate the D:Archives panel when both A and B archives are ready."""
        panel = getattr(self, "_diff_archives_panel", None)
        if panel is None:
            return

        a_info = self._tab_dispatch.get("A:Archives")
        b_info = self._tab_dispatch.get("B:Archives")
        if not a_info or not b_info:
            return

        wabba_a = a_info.get("wabba")
        wabba_b = b_info.get("wabba")

        cache_a = wabba_a.cache if wabba_a else None
        cache_b = wabba_b.cache if wabba_b else None

        a_ready = cache_a is not None and cache_a.archives_ready.is_set()
        b_ready = cache_b is not None and cache_b.archives_ready.is_set()

        if not a_ready or not b_ready:
            def _retry() -> None:
                try:
                    current = self._main_nb.tab(self._main_nb.select(), "text")
                except tk.TclError:
                    return
                if current == "D:Archives":
                    self._try_populate_diff_archives()
            self.after(150, _retry)
            return

        self._populate_diff_archives(cache_a, cache_b)

    def _populate_diff_archives(self, cache_a, cache_b) -> None:
        """Compute the archive diff between A and B and load the D:Archives panel."""
        panel = getattr(self, "_diff_archives_panel", None)
        if panel is None:
            return

        hashes_a = set(cache_a.archives_by_hash.keys())
        hashes_b = set(cache_b.archives_by_hash.keys())

        only_in_a = hashes_a - hashes_b
        only_in_b = hashes_b - hashes_a

        # ── Detect "update" pairs ────────────────────────────────────────
        # Two archives are treated as the same mod being updated when they
        # are A-only and B-only respectively and share the same mod key.
        # Supported sources:
        #   • Nexus  – State.$type contains "NexusDownloader", key = ("nexus", ModID)
        #   • LoversLab – State.Url matches
        #                 https[s]://[www.]loverslab.com/files/file/<id>[...],
        #                 key = ("ll", id)
        #   • WabbajackAuthored – State.Url starts with
        #                 https://authored-files.wabbajack.org/<name>[_-]...,
        #                 key = ("wj", prefix_url_up_to_first_dash_or_underscore)
        #                 %20 is normalised to space; trailing version numbers of the
        #                 form " 7.30.7z" are stripped to ".7z" so that
        #                 "BakaFactory SLAL Animation.7z_<uuid>" and
        #                 "BakaFactory%20SLAL%20Animation%207.30.7z_<uuid>"
        #                 both produce the same key.
        # Only 1-to-1 pairings are matched; if a key appears more than once
        # on either side all its entries are kept as individual items.

        import re as _re
        _LL_RE = _re.compile(r"https?://(?:www\.)?loverslab\.com/files/file/(\d+)")
        _WJ_PREFIX = "https://authored-files.wabbajack.org/"
        _WJ_FILE_RE = _re.compile(r"[^_\-]+")
        _WJ_VER_RE = _re.compile(r"\s+\d[\d.]*(\.\w+)$")

        def _mod_key(archive: dict) -> "tuple | None":
            state = archive.get("State")
            if not isinstance(state, dict):
                return None
            # Nexus
            if "NexusDownloader" in state.get("$type", ""):
                mid = state.get("ModID")
                if mid is not None:
                    return ("nexus", mid)
            url = state.get("Url") or ""
            # LoversLab
            m = _LL_RE.match(url)
            if m:
                return ("ll", int(m.group(1)))
            # authored-files.wabbajack.org
            if url.startswith(_WJ_PREFIX):
                m = _WJ_FILE_RE.match(url[len(_WJ_PREFIX):])
                if m:
                    base = m.group(0).replace("%20", " ")
                    base = _WJ_VER_RE.sub(r"\1", base)
                    return ("wj", _WJ_PREFIX + base)
            return None

        # mod key → list of archive dicts for A-only / B-only sides
        mods_a: dict[tuple, list[dict]] = {}
        for h in only_in_a:
            arch = cache_a.archives_by_hash[h]
            key = _mod_key(arch)
            if key is not None:
                mods_a.setdefault(key, []).append(arch)

        mods_b: dict[tuple, list[dict]] = {}
        for h in only_in_b:
            arch = cache_b.archives_by_hash[h]
            key = _mod_key(arch)
            if key is not None:
                mods_b.setdefault(key, []).append(arch)

        # Only merge when exactly one entry per side shares that key.
        shared_mod_ids = {
            key
            for key in set(mods_a) & set(mods_b)
            if len(mods_a[key]) == 1 and len(mods_b[key]) == 1
        }

        # Hashes absorbed into "updated" pairs (excluded from removed/added).
        updated_hashes_a = {mods_a[key][0]["Hash"] for key in shared_mod_ids}
        updated_hashes_b = {mods_b[key][0]["Hash"] for key in shared_mod_ids}

        diff_items: list[dict] = []

        # Add "updated" entries, sorted by mod name for readability.
        def _mod_sort_key(key: tuple) -> str:
            arch = mods_a[key][0]
            state = arch.get("State") or {}
            return (state.get("Name") or arch.get("Name", "")).lower()

        for mid in sorted(shared_mod_ids, key=_mod_sort_key):
            arch_a = mods_a[mid][0]
            arch_b = mods_b[mid][0]
            state_a = arch_a.get("State") or {}
            state_b = arch_b.get("State") or {}
            item = dict(arch_a)
            item["_diff_side"] = "updated"
            item["_diff_wabba"] = "A"
            item["_ver_a"] = state_a.get("Version", "?") or "?"
            item["_ver_b"] = state_b.get("Version", "?") or "?"
            item["_b_archive"] = dict(arch_b)
            diff_items.append(item)

        # Add remaining A-only items (not part of an update pair).
        for h in sorted(only_in_a - updated_hashes_a):
            item = dict(cache_a.archives_by_hash[h])
            item["_diff_side"] = "removed"
            diff_items.append(item)

        # Add remaining B-only items (not part of an update pair).
        for h in sorted(only_in_b - updated_hashes_b):
            item = dict(cache_b.archives_by_hash[h])
            item["_diff_side"] = "added"
            diff_items.append(item)

        panel.load_items(diff_items)
        print(
            f"[tab] loaded: D:Archives  "
            f"({len(shared_mod_ids)} updated, "
            f"{len(only_in_a - updated_hashes_a)} A-only, "
            f"{len(only_in_b - updated_hashes_b)} B-only)"
        )

    # ------------------------------------------------------------------
    # D:Directives diff tab (compare mode)
    # ------------------------------------------------------------------

    def _try_populate_diff_directives(self) -> None:
        """Populate the D:Directives panel when both A and B directives are ready.

        Waits for both ``cache_a.directives_ready`` and
        ``cache_b.directives_ready`` (which also guarantee that
        ``wabba_root_info`` is populated since it is built during
        ``run_prep``, which ``run_directives_prep`` waits on).
        """
        panel = getattr(self, "_diff_directives_panel", None)
        if panel is None:
            return

        info = self._tab_dispatch.get("D:Directives")
        if not info:
            return

        wabba_a = info.get("wabba_a")
        wabba_b = info.get("wabba_b")

        cache_a = wabba_a.cache if wabba_a else None
        cache_b = wabba_b.cache if wabba_b else None

        a_ready = cache_a is not None and cache_a.directives_ready.is_set()
        b_ready = cache_b is not None and cache_b.directives_ready.is_set()

        if not a_ready or not b_ready:
            def _retry() -> None:
                try:
                    current = self._main_nb.tab(self._main_nb.select(), "text")
                except tk.TclError:
                    return
                if current == "D:Directives":
                    self._try_populate_diff_directives()
            self.after(150, _retry)
            return

        self._populate_diff_directives(cache_a, cache_b)

    def _populate_diff_directives(self, cache_a, cache_b) -> None:
        """Compute the directive diff between A and B and load the D:Directives panel."""
        import threading as _threading
        from ..wabba.diff import diff_directives

        panel = getattr(self, "_diff_directives_panel", None)
        if panel is None:
            return

        panel.set_loading()

        def _worker() -> None:
            diff_items = diff_directives(cache_a, cache_b)
            a_only = sum(1 for i in diff_items if i.get("_diff_side") == "A only")
            b_only = sum(1 for i in diff_items if i.get("_diff_side") == "B only")
            changed = sum(1 for i in diff_items if i.get("_diff_side") == "changed") // 2
            self.after(0, lambda: _load(diff_items, a_only, b_only, changed))

        def _load(diff_items, a_only, b_only, changed) -> None:
            p = getattr(self, "_diff_directives_panel", None)
            if p is None:
                return
            p.load_items(diff_items)
            print(
                f"[tab] loaded: D:Directives  "
                f"({a_only} A-only, {b_only} B-only, {changed} changed pairs, "
                f"{len(diff_items)} total rows)"
            )

        _threading.Thread(target=_worker, daemon=True).start()
