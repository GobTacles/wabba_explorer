"""Mixin for the 'Archives' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .filtered_list_panel import _FilteredListPanel
from .gui_util import _archive_label, _item_matches, _archive_item_matches


class _TabArchives:
    """Builds and drives the 'Archives' tab."""

    def _build_tab_archives(self, tab_label: str = "Archives", wabba=None) -> None:
        """Archives list (Name [Hash]) with filter + JSON preview.

        *wabba* is the WabbaFile this tab is bound to.  ``None`` means
        single-file mode (uses ``self._wabba`` dynamically).  All callbacks
        are closures so they always reference this tab's wabba/widgets even
        in compare mode where two Archives tabs coexist.
        """
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text=tab_label)

        # Ordered list of (key, base_label, $type substring).
        # "other" has an empty substring — it is the catch-all.
        type_specs: list[tuple[str, str, str]] = [
            ("nexus",     "Nexus",     "NexusDownloader"),
            ("manual",    "Manual",    "ManualDownloader"),
            ("game",      "Game",      "GameFileSourceDownloader"),
            ("http",      "Http",      "HttpDownloader"),
            ("cdn",       "CDN",       "WabbajackCDNDownloader"),
            ("mega",      "Mega",      "MegaDownloader"),
            ("gdrive",    "GDrive",    "GoogleDriveDownloader"),
            ("mediafire", "MediaFire", "MediaFireDownloader"),
            ("other",     "Other",     ""),
        ]
        filter_vars: dict[str, tk.BooleanVar] = {
            key: tk.BooleanVar(value=True) for key, _, _ in type_specs
        }
        filter_label_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value=label) for key, label, _ in type_specs
        }

        # Mutable containers for widgets created during build callbacks.
        panel_ref: list = [None]
        meta_direct_btn_ref: list = [None]
        meta_btn_ref: list = [None]
        open_browser_btn_ref: list = [None]
        copy_url_btn_ref: list = [None]
        url_var_ref: list = [None]

        def _get_wabba():
            return wabba if wabba is not None else self._wabba

        # ── Type-filter checkboxes ───────────────────────────────────────

        def _build_type_filters(parent: ttk.Frame) -> None:
            n = len(type_specs)
            chunk = (n + 2) // 3
            rows = [type_specs[i:i + chunk] for i in range(0, n, chunk)]
            for row_specs in rows:
                bar = ttk.Frame(parent)
                bar.pack(fill=tk.X, pady=(2, 0))
                ttk.Label(bar, text="Show:").pack(side=tk.LEFT)
                for key, _label, _type_str in row_specs:
                    ttk.Checkbutton(
                        bar,
                        textvariable=filter_label_vars[key],
                        variable=filter_vars[key],
                        command=lambda: panel_ref[0].refresh_filter() if panel_ref[0] else None,
                    ).pack(side=tk.LEFT, padx=2)

        def _type_filter(item: object) -> bool:
            if not isinstance(item, dict):
                return True
            state = item.get("State")
            state_type = state.get("$type", "") if isinstance(state, dict) else ""
            for key, _label, type_str in type_specs:
                if type_str and type_str in state_type:
                    return bool(filter_vars[key].get())
            return bool(filter_vars["other"].get())

        # ── Extra info (directives referencing this archive) ─────────────

        def _extra_info(archive_item: dict) -> str:
            from ..wabba.entry_info import get_archive_directives_text
            from ..wabba.generate_meta import generate_meta
            w = _get_wabba()
            cache = w.cache if w else None
            directives = cache.directives if cache else []
            parts = []
            directives_text = get_archive_directives_text(archive_item, directives)
            if directives_text:
                parts.append(directives_text)
            meta_content = generate_meta(archive_item)
            if meta_content is not None:
                parts.append(
                    "=== generated .meta for downloads folder (experimental)\n" + meta_content
                )
            return "\n\n".join(parts)

        # ── Tool buttons ─────────────────────────────────────────────────

        def _get_url(item: object) -> "str | None":
            """Return a URL for *item*, or None.  (Pure – no self.* needed.)"""
            if not isinstance(item, dict):
                return None
            state = item.get("State")
            if not isinstance(state, dict):
                return None
            state_type = state.get("$type", "")
            if "NexusDownloader" in state_type:
                mod_id = state.get("ModID")
                file_id = state.get("FileID")
                if mod_id is not None and file_id is not None:
                    return (
                        f"https://www.nexusmods.com/skyrimspecialedition/mods/{mod_id}"
                        f"?tab=files&file_id={file_id}"
                    )
            from ..wabba.generate_meta import generate_meta
            meta_content = generate_meta(item)
            if meta_content:
                for line in meta_content.splitlines():
                    if line.startswith("directURL="):
                        return line[len("directURL="):]
            return state.get("Url") or None

        def _build_tools(tools_frame: ttk.Frame) -> None:
            btn_row = ttk.Frame(tools_frame)
            btn_row.pack(fill=tk.X)

            meta_direct_btn = ttk.Button(
                btn_row,
                text="extract .meta for downloads folder",
                state=tk.DISABLED,
                command=_on_meta_direct_click,
            )
            meta_direct_btn.pack(side=tk.LEFT, padx=2, pady=2)
            meta_direct_btn_ref[0] = meta_direct_btn

            meta_btn = ttk.Button(
                btn_row,
                text="generate .meta for downloads folder (experimental)",
                state=tk.DISABLED,
                command=_on_meta_click,
            )
            meta_btn.pack(side=tk.LEFT, padx=2, pady=2)
            meta_btn_ref[0] = meta_btn

            url_row = ttk.Frame(tools_frame)
            url_row.pack(fill=tk.X, pady=(2, 0))

            ttk.Label(url_row, text="URL:").pack(side=tk.LEFT)
            url_var = tk.StringVar()
            url_var_ref[0] = url_var
            ttk.Entry(
                url_row,
                textvariable=url_var,
                state="readonly",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

            open_btn = ttk.Button(
                url_row,
                text="open in browser",
                state=tk.DISABLED,
                command=_on_open_browser_click,
            )
            open_btn.pack(side=tk.LEFT, padx=2)
            open_browser_btn_ref[0] = open_btn

            copy_btn = ttk.Button(
                url_row,
                text="copy",
                state=tk.DISABLED,
                command=_on_copy_url_click,
            )
            copy_btn.pack(side=tk.LEFT, padx=(0, 2))
            copy_url_btn_ref[0] = copy_btn

        def _on_item_changed(item) -> None:
            from ..wabba.generate_meta import generate_meta
            meta_btn = meta_btn_ref[0]
            if meta_btn is not None:
                has_generated_meta = isinstance(item, dict) and generate_meta(item) is not None
                meta_btn.configure(state=tk.NORMAL if has_generated_meta else tk.DISABLED)

            meta_direct_btn = meta_direct_btn_ref[0]
            if meta_direct_btn is not None:
                state_obj = item.get("State") if isinstance(item, dict) else None
                state_type = state_obj.get("$type", "") if isinstance(state_obj, dict) else ""
                is_game_file = "GameFileSourceDownloader" in state_type
                has_meta = (
                    not is_game_file
                    and isinstance(item, dict)
                    and isinstance(item.get("Meta"), str)
                    and item["Meta"].strip() != ""
                )
                meta_direct_btn.configure(state=tk.NORMAL if has_meta else tk.DISABLED)

            url = _get_url(item)
            url_var = url_var_ref[0]
            if url_var is not None:
                url_var.set(url or "")
            for btn in (open_browser_btn_ref[0], copy_url_btn_ref[0]):
                if btn is not None:
                    btn.configure(state=tk.NORMAL if url else tk.DISABLED)

        def _on_meta_click() -> None:
            from tkinter import filedialog, messagebox
            from ..wabba.generate_meta import generate_meta
            p = panel_ref[0]
            if p is None:
                return
            item = p.get_selected_item()
            if not isinstance(item, dict):
                return
            name = item.get("Name", "archive")
            base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            save_path = filedialog.asksaveasfilename(
                initialfile=base + ".meta",
                title="generate .meta for downloads folder",
            )
            if not save_path:
                return
            try:
                content = generate_meta(item)
                if content is None:
                    return
                with open(save_path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(content)
            except Exception as exc:
                messagebox.showerror("generate .meta", f"Failed to save:\n{exc}")

        def _on_meta_direct_click() -> None:
            from tkinter import filedialog, messagebox
            p = panel_ref[0]
            if p is None:
                return
            item = p.get_selected_item()
            if not isinstance(item, dict):
                return
            meta = item.get("Meta", "")
            if not isinstance(meta, str) or not meta.strip():
                return
            name = item.get("Name", "archive")
            base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            save_path = filedialog.asksaveasfilename(
                initialfile=base + ".meta",
                title="extract .meta for downloads folder",
            )
            if not save_path:
                return
            try:
                content = meta.replace("\\n", "\n")
                if not content.endswith("\n"):
                    content += "\n"
                with open(save_path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(content)
            except Exception as exc:
                messagebox.showerror("extract .meta", f"Failed to save:\n{exc}")

        def _on_open_browser_click() -> None:
            import webbrowser
            url_var = url_var_ref[0]
            if url_var is not None:
                url = url_var.get()
                if url:
                    webbrowser.open(url)

        def _on_copy_url_click() -> None:
            url_var = url_var_ref[0]
            if url_var is not None:
                url = url_var.get()
                if url:
                    self.clipboard_clear()
                    self.clipboard_append(url)

        # ── Build the panel ───────────────────────────────────────────────

        archives_panel = _FilteredListPanel(
            frame,
            label_fn=_archive_label,
            filter_fn=lambda item, t, pat: _archive_item_matches(item, t, pat),
            extra_info_fn=_extra_info,
            extra_controls_fn=_build_type_filters,
            item_filter_fn=_type_filter,
            tools_fn=_build_tools,
            on_item_changed=_on_item_changed,
        )
        panel_ref[0] = archives_panel
        archives_panel.pack(fill=tk.BOTH, expand=True)

        # Single-file mode: keep self.* pointing at the (only) tab.
        if wabba is None:
            self._archives_panel = archives_panel
            self._archive_type_specs = type_specs
            self._archives_filter_vars = filter_vars
            self._archives_filter_label_vars = filter_label_vars
            self._archives_meta_direct_btn = meta_direct_btn_ref[0]
            self._archives_meta_btn = meta_btn_ref[0]
            self._archives_open_browser_btn = open_browser_btn_ref[0]
            self._archives_copy_url_btn = copy_url_btn_ref[0]
            self._archives_url_var = url_var_ref[0]

        self._tab_dispatch[tab_label] = {
            "type": "Archives",
            "wabba": wabba,
            "panel": archives_panel,
            "type_specs": type_specs,
            "filter_vars": filter_vars,
            "filter_label_vars": filter_label_vars,
        }

    def update_archive_filter_counts(
        self,
        archives: list,
        *,
        type_specs: "list | None" = None,
        filter_label_vars: "dict | None" = None,
    ) -> None:
        """Recompute per-type counts and update checkbox labels.

        When called without keyword arguments the single-file-mode attrs are
        used (``self._archive_type_specs`` / ``self._archives_filter_label_vars``).
        In compare mode the caller supplies the per-tab values.
        """
        if type_specs is None:
            type_specs = self._archive_type_specs
        if filter_label_vars is None:
            filter_label_vars = self._archives_filter_label_vars
        counts: dict[str, int] = {key: 0 for key, _, _ in type_specs}
        for item in archives:
            if not isinstance(item, dict):
                continue
            state = item.get("State")
            state_type = state.get("$type", "") if isinstance(state, dict) else ""
            matched = False
            for key, _label, type_str in type_specs:
                if type_str and type_str in state_type:
                    counts[key] += 1
                    matched = True
                    break
            if not matched:
                counts["other"] += 1
        for key, base_label, _type_str in type_specs:
            filter_label_vars[key].set(f"{base_label} ({counts[key]})")
