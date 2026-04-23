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

    def _build_tab_directives(self) -> None:
        """Directives list (To [Hash]) with filter + JSON preview."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="Directives")

        self._dir_show_inline = tk.BooleanVar(value=True)
        self._dir_show_metaini = tk.BooleanVar(value=True)
        self._dir_show_fromarchive = tk.BooleanVar(value=True)
        self._dir_show_patchedfromarchive = tk.BooleanVar(value=True)
        self._dir_show_other = tk.BooleanVar(value=True)

        def _is_meta_ini(item: dict) -> bool:
            """Return True if item is an InlineFile under mods/.../meta.ini."""
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
                ("InlineFile", self._dir_show_inline),
                ("FromArchive", self._dir_show_fromarchive),
                ("PatchedFromArchive", self._dir_show_patchedfromarchive),
                ("Other", self._dir_show_other),
            ):
                ttk.Checkbutton(
                    cb_frame, text=text, variable=var,
                    command=self._on_directive_type_filter_change,
                ).pack(side=tk.LEFT)
            # meta.ini is a sub-filter of InlineFile
            self._dir_metaini_cb = ttk.Checkbutton(
                cb_frame, text="  meta.ini", variable=self._dir_show_metaini,
                command=self._on_directive_type_filter_change,
            )
            self._dir_metaini_cb.pack(side=tk.LEFT, padx=(4, 0))

        def _type_gate(item: dict) -> bool:
            t = item.get("$type", "")
            if t == "InlineFile":
                if _is_meta_ini(item):
                    return self._dir_show_metaini.get()
                return self._dir_show_inline.get()
            if t == "FromArchive":
                return self._dir_show_fromarchive.get()
            if t == "PatchedFromArchive":
                return self._dir_show_patchedfromarchive.get()
            return self._dir_show_other.get()

        def _directive_extra_info(item: dict) -> str:
            return self._directive_detail(item)

        def _tools(tools_frame: ttk.Frame) -> None:
            self._dir_extract_btn = ttk.Button(
                tools_frame,
                text="Extract InlineFile",
                state=tk.DISABLED,
                command=self._do_extract_directive,
            )
            self._dir_extract_btn.pack(side=tk.LEFT, padx=2, pady=2)

        def _on_directive_item_changed(item) -> None:
            btn = self._dir_extract_btn
            if btn is None:
                return
            if item is not None and self._wabba is not None and _get_extract_source_id(item):
                btn.configure(state=tk.NORMAL)
            else:
                btn.configure(state=tk.DISABLED)

        self._directives_panel = _FilteredListPanel(
            frame,
            label_fn=_directive_label,
            filter_fn=lambda item, t, pat: _item_matches(item, t, pat, "To", "Hash"),
            extra_controls_fn=_type_controls,
            item_filter_fn=_type_gate,
            extra_info_fn=_directive_extra_info,
            tools_fn=_tools,
            on_item_changed=_on_directive_item_changed,
        )
        self._directives_panel.pack(fill=tk.BOTH, expand=True)

    def _on_directive_type_filter_change(self) -> None:
        """Re-apply the filter whenever a $type checkbox is toggled."""
        self._directives_panel._do_filter()  # noqa: SLF001

    def _directive_detail(self, item: dict) -> str:
        """Rich extra-info for the Directives tab detail pane."""
        cache = self._wabba.cache if self._wabba else None
        archives_by_hash = cache.archives_by_hash if cache else {}
        return get_directive_detail_text(item, archives_by_hash, self._wabba)

    def _do_extract_directive(self) -> None:
        """Extract the inline/patch file for the currently selected directive."""
        item = self._directives_panel.get_selected_item()
        if item is None or self._wabba is None:
            return
        source_id = _get_extract_source_id(item)
        if not source_id:
            return
        to = item.get("To", "")
        default_name = to.replace("\\", "/").rsplit("/", 1)[-1] if to else source_id
        if item.get("$type") == "PatchedFromArchive":
            default_name += ".octodelta"
        _do_extract_inline(self._wabba, source_id, default_name)
