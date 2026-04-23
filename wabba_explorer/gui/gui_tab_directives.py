"""Mixin for the 'Directives' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..wabba.entry_info import get_directive_detail_text
from .filtered_list_panel import _FilteredListPanel
from .gui_util import (
    _directive_label,
    _item_matches,
    _get_extract_source_id,
    _do_extract_inline,
)


class _TabDirectives:
    """Builds and drives the 'Directives' tab."""

    def _build_tab_directives(self, tab_label: str = "Directives", wabba=None) -> None:
        """Directives list (To [Hash]) with filter + JSON preview.

        *wabba* is the WabbaFile this tab is bound to.  ``None`` means
        single-file mode (uses ``self._wabba`` dynamically).  All callbacks
        are built as closures so they always reference this tab's wabba even
        in compare mode where two Directives tabs coexist.
        """
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text=tab_label)

        # Per-tab type-filter variables.
        dir_show_inline = tk.BooleanVar(value=True)
        dir_show_metaini = tk.BooleanVar(value=True)
        dir_show_fromarchive = tk.BooleanVar(value=True)
        dir_show_patchedfromarchive = tk.BooleanVar(value=True)
        dir_show_other = tk.BooleanVar(value=True)

        # Mutable container so the panel closure can reference the panel before
        # the _FilteredListPanel object exists.
        panel_ref: list = [None]
        extract_btn_ref: list = [None]

        def _get_wabba():
            return wabba if wabba is not None else self._wabba

        def _is_meta_ini(item: dict) -> bool:
            to = item.get("To", "").replace("\\", "/")
            parts = to.lower().split("/")
            return (
                len(parts) >= 3
                and parts[0] == "mods"
                and parts[-1] == "meta.ini"
            )

        def _type_controls(left_frame: ttk.Frame) -> None:
            cb_frame = ttk.Frame(left_frame)
            cb_frame.pack(fill=tk.X, pady=(2, 0))
            for text, var in (
                ("InlineFile", dir_show_inline),
                ("meta.ini", dir_show_metaini),
                ("FromArchive", dir_show_fromarchive),
                ("PatchedFromArchive", dir_show_patchedfromarchive),
                ("Other", dir_show_other),
            ):
                ttk.Checkbutton(
                    cb_frame, text=text, variable=var,
                    command=lambda: panel_ref[0]._do_filter() if panel_ref[0] else None,  # noqa: SLF001
                ).pack(side=tk.LEFT, padx=(2, 0))

        def _type_gate(item: dict) -> bool:
            t = item.get("$type", "")
            if t == "InlineFile":
                if _is_meta_ini(item):
                    return bool(dir_show_metaini.get())
                return bool(dir_show_inline.get())
            if t == "FromArchive":
                return bool(dir_show_fromarchive.get())
            if t == "PatchedFromArchive":
                return bool(dir_show_patchedfromarchive.get())
            return bool(dir_show_other.get())

        def _directive_extra_info(item: dict) -> str:
            w = _get_wabba()
            cache = w.cache if w else None
            archives_by_hash = cache.archives_by_hash if cache else {}
            return get_directive_detail_text(item, archives_by_hash, w)

        def _do_extract() -> None:
            p = panel_ref[0]
            if p is None:
                return
            item = p.get_selected_item()
            if item is None:
                return
            w = _get_wabba()
            if w is None:
                return
            source_id = _get_extract_source_id(item)
            if not source_id:
                return
            to = item.get("To", "")
            default_name = to.replace("\\", "/").rsplit("/", 1)[-1] if to else source_id
            if item.get("$type") == "PatchedFromArchive":
                default_name += ".octodelta"
            _do_extract_inline(w, source_id, default_name)

        def _tools(tools_frame: ttk.Frame) -> None:
            btn = ttk.Button(
                tools_frame,
                text="Extract InlineFile",
                state=tk.DISABLED,
                command=_do_extract,
            )
            btn.pack(side=tk.LEFT, padx=2, pady=2)
            extract_btn_ref[0] = btn

        def _on_directive_item_changed(item) -> None:
            btn = extract_btn_ref[0]
            if btn is None:
                return
            w = _get_wabba()
            if item is not None and w is not None and _get_extract_source_id(item):
                btn.configure(state=tk.NORMAL)
            else:
                btn.configure(state=tk.DISABLED)

        directives_panel = _FilteredListPanel(
            frame,
            label_fn=_directive_label,
            filter_fn=lambda item, t, pat: _item_matches(item, t, pat, "To", "Hash"),
            extra_controls_fn=_type_controls,
            item_filter_fn=_type_gate,
            extra_info_fn=_directive_extra_info,
            tools_fn=_tools,
            on_item_changed=_on_directive_item_changed,
        )
        panel_ref[0] = directives_panel
        directives_panel.pack(fill=tk.BOTH, expand=True)

        # Single-file mode: keep self.* pointing at the (only) tab.
        if wabba is None:
            self._directives_panel = directives_panel
            self._dir_show_inline = dir_show_inline
            self._dir_show_metaini = dir_show_metaini
            self._dir_show_fromarchive = dir_show_fromarchive
            self._dir_show_patchedfromarchive = dir_show_patchedfromarchive
            self._dir_show_other = dir_show_other
            self._dir_extract_btn = extract_btn_ref[0]

        self._tab_dispatch[tab_label] = {
            "type": "Directives",
            "wabba": wabba,
            "panel": directives_panel,
            "show_vars": {
                "inline": dir_show_inline,
                "metaini": dir_show_metaini,
                "fromarchive": dir_show_fromarchive,
                "patchedfromarchive": dir_show_patchedfromarchive,
                "other": dir_show_other,
            },
        }
