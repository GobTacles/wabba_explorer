"""Tab-change, polling, per-tab population and filter-sync methods."""

from __future__ import annotations

import time
import tkinter as tk

from ..wabba.cache import WabbaCache


class _BackgroundTabsMixin:
    """Mixin: tab-change handling, polling, archives/directives loading, filter sync."""

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
            prev = getattr(self, "_prev_tab_text", "")
            if prev:
                self._do_filter_sync(prev, tab_text)
            self._prev_tab_text = tab_text
            # Only print when the tab actually changes to avoid flooding the
            # console with duplicate messages during programmatic tab rebuilds.
            if tab_text != prev:
                print(f"[tab] opened: {tab_text}")

        if tab_text == "D:Archives":
            self._try_populate_diff_archives()
            return

        if tab_text == "D:Directives":
            self._try_populate_diff_directives()
            return

        info = self._tab_dispatch.get(tab_text)
        if info is None:
            return

        # Resolve the wabba: explicit compare binding OR the single-file self._wabba.
        wabba = info.get("wabba") or self._wabba
        cache = wabba.cache if wabba else None
        tab_type = info.get("type")
        panel = info.get("panel")

        if tab_type == "Archives" and panel is not None:
            if cache is None:
                return
            if cache.archives_ready.is_set():
                self._load_archives_tab_for(
                    cache, panel,
                    info.get("type_specs"),
                    info.get("filter_label_vars"),
                    tab_text,
                )
            else:
                self._poll_tab_ready(
                    tab_text, cache, cache.archives_ready,
                    lambda c, _p=panel, _i=info, _tt=tab_text: self._load_archives_tab_for(
                        c, _p, _i.get("type_specs"), _i.get("filter_label_vars"), _tt
                    ),
                )

        elif tab_type == "Directives" and panel is not None:
            if cache is None:
                return
            if cache.directives_ready.is_set():
                self._populate_directives_tab_for(cache, panel, tab_text)
            else:
                self._poll_tab_ready(
                    tab_text, cache, cache.directives_ready,
                    lambda c, _p=panel, _tt=tab_text: self._populate_directives_tab_for(c, _p, _tt),
                )

        elif tab_type == "Files" and panel is not None:
            if cache is None:
                return
            t0 = self._tab_open_times.get(tab_text)
            if cache.files_ready.is_set():
                panel.load_from_precomputed(wabba, cache, t0=t0)
            else:
                self._poll_tab_ready(
                    tab_text, cache, cache.files_ready,
                    lambda c, _p=panel, _w=wabba, _tt=tab_text: _p.load_from_precomputed(
                        _w, c, t0=self._tab_open_times.get(_tt)
                    ),
                )
        # "Main" and "Problems" tabs are populated via callbacks, not polling.

    def _poll_tab_ready(
        self,
        tab_name: str,
        cache: WabbaCache,
        event: "threading.Event",
        callback,
        delay_ms: int = 150,
    ) -> None:
        """Poll *event* every *delay_ms* ms; call *callback(cache)* when set."""
        def _check() -> None:
            if cache.cancelled:
                return
            if event.is_set():
                try:
                    current = self._main_nb.tab(self._main_nb.select(), "text")
                except tk.TclError:
                    return
                if current == tab_name:
                    callback(cache)
            else:
                self.after(delay_ms, _check)

        self.after(delay_ms, _check)

    def _load_archives_tab_for(
        self,
        cache: WabbaCache,
        panel,
        type_specs: "list | None",
        filter_label_vars: "dict | None",
        tab_text: str = "Archives",
    ) -> None:
        """Populate an Archives panel from the pre-built model."""
        t0 = self._tab_open_times.get(tab_text)
        if cache.archive_model is not None:
            panel.load_model(cache.archive_model)
        else:
            panel.load_items(cache.archives)
        if type_specs and filter_label_vars:
            self.update_archive_filter_counts(
                cache.archives,
                type_specs=type_specs,
                filter_label_vars=filter_label_vars,
            )
        elapsed = int((time.monotonic() - (t0 or time.monotonic())) * 1000)
        print(f"[tab] loaded: Archives  ({len(cache.archives)} entries, {elapsed} ms)")

    def _populate_directives_tab_for(
        self,
        cache: WabbaCache,
        panel,
        tab_text: str = "Directives",
    ) -> None:
        """Populate a Directives panel."""
        t0 = self._tab_open_times.get(tab_text)
        if cache.directive_model is not None:
            panel.load_model(cache.directive_model)
        else:
            panel.load_items(cache.directives)
        elapsed = int((time.monotonic() - (t0 or time.monotonic())) * 1000)
        print(f"[tab] loaded: Directives  ({len(cache.directives)} entries, {elapsed} ms)")

    # ------------------------------------------------------------------
    # Filter sync between A/B tabs of the same type
    # ------------------------------------------------------------------

    def _do_filter_sync(self, from_text: str, to_text: str) -> None:
        """Copy filter state from *from_text* tab to *to_text* tab when they
        are an A/B pair of the same type (e.g. "A:Files" → "B:Files")."""
        from_info = self._tab_dispatch.get(from_text)
        to_info = self._tab_dispatch.get(to_text)
        if not from_info or not to_info:
            return
        if from_info.get("type") != to_info.get("type"):
            return

        tab_type = from_info["type"]

        if tab_type == "Files":
            fp = from_info.get("panel")
            tp = to_info.get("panel")
            if fp and tp:
                tp.set_filter_text(fp.get_filter_text())
                for attr in (
                    "_fs_show_inline",
                    "_fs_show_fromarchive",
                    "_fs_show_patched",
                    "_fs_show_other",
                ):
                    fv = getattr(fp, attr, None)
                    tv = getattr(tp, attr, None)
                    if fv and tv:
                        tv.set(fv.get())

        elif tab_type == "Archives":
            fp = from_info.get("panel")
            tp = to_info.get("panel")
            if fp and tp:
                tp.set_filter_text(fp.get_filter_text())
            fv = from_info.get("filter_vars", {})
            tv = to_info.get("filter_vars", {})
            for k in fv:
                if k in tv:
                    tv[k].set(fv[k].get())

        elif tab_type == "Directives":
            fp = from_info.get("panel")
            tp = to_info.get("panel")
            if fp and tp:
                tp.set_filter_text(fp.get_filter_text())
            fsv = from_info.get("show_vars", {})
            tsv = to_info.get("show_vars", {})
            for k in fsv:
                if k in tsv:
                    tsv[k].set(fsv[k].get())
