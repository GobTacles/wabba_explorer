"""Mixin for the 'Archives' tab of WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .filtered_list_panel import _FilteredListPanel
from .gui_util import _archive_label, _item_matches


class _TabArchives:
    """Builds and drives the 'Archives' tab."""

    def _build_tab_archives(self) -> None:
        """Archives list (Name [Hash]) with filter + JSON preview."""
        frame = ttk.Frame(self._main_nb)
        self._main_nb.add(frame, text="Archives")

        self._archives_meta_direct_btn: ttk.Button | None = None
        self._archives_meta_btn: ttk.Button | None = None
        self._archives_open_browser_btn: ttk.Button | None = None
        self._archives_copy_url_btn: ttk.Button | None = None
        self._archives_url_var: tk.StringVar | None = None

        # Ordered list of (key, base_label, $type substring).
        # "other" has an empty substring — it is the catch-all.
        self._archive_type_specs: list[tuple[str, str, str]] = [
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
        self._archives_filter_vars: dict[str, tk.BooleanVar] = {
            key: tk.BooleanVar(value=True) for key, _, _ in self._archive_type_specs
        }
        self._archives_filter_label_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value=label)
            for key, label, _ in self._archive_type_specs
        }

        self._archives_panel = _FilteredListPanel(
            frame,
            label_fn=_archive_label,
            filter_fn=lambda item, t, pat: _item_matches(item, t, pat, "Name", "Hash"),
            extra_info_fn=self._archive_extra_info,
            extra_controls_fn=self._build_archive_type_filters,
            item_filter_fn=self._archive_type_filter,
            tools_fn=self._build_archive_tools,
            on_item_changed=self._on_archive_item_changed,
        )
        self._archives_panel.pack(fill=tk.BOTH, expand=True)

    def _build_archive_type_filters(self, parent: ttk.Frame) -> None:
        """Add type checkbox filters below the list (three rows)."""
        specs = self._archive_type_specs
        n = len(specs)
        chunk = (n + 2) // 3  # ceiling-divide into 3 roughly equal rows
        rows = [specs[i:i + chunk] for i in range(0, n, chunk)]
        for row_specs in rows:
            bar = ttk.Frame(parent)
            bar.pack(fill=tk.X, pady=(2, 0))
            ttk.Label(bar, text="Show:").pack(side=tk.LEFT)
            for key, _label, _type_str in row_specs:
                ttk.Checkbutton(
                    bar,
                    textvariable=self._archives_filter_label_vars[key],
                    variable=self._archives_filter_vars[key],
                    command=self._on_archive_type_filter_changed,
                ).pack(side=tk.LEFT, padx=2)

    def _on_archive_type_filter_changed(self) -> None:
        self._archives_panel.refresh_filter()

    def _archive_type_filter(self, item: object) -> bool:
        """Return True if *item* passes the current type-checkbox filter."""
        if not isinstance(item, dict):
            return True
        state = item.get("State")
        state_type = state.get("$type", "") if isinstance(state, dict) else ""
        for key, _label, type_str in self._archive_type_specs:
            if type_str and type_str in state_type:
                return bool(self._archives_filter_vars[key].get())
        # catch-all: "other"
        return bool(self._archives_filter_vars["other"].get())

    def update_archive_filter_counts(self, archives: list) -> None:
        """Recompute per-type counts and update checkbox labels."""
        counts: dict[str, int] = {key: 0 for key, _, _ in self._archive_type_specs}
        for item in archives:
            if not isinstance(item, dict):
                continue
            state = item.get("State")
            state_type = state.get("$type", "") if isinstance(state, dict) else ""
            matched = False
            for key, _label, type_str in self._archive_type_specs:
                if type_str and type_str in state_type:
                    counts[key] += 1
                    matched = True
                    break
            if not matched:
                counts["other"] += 1
        for key, base_label, _type_str in self._archive_type_specs:
            self._archives_filter_label_vars[key].set(f"{base_label} ({counts[key]})")

    def _build_archive_tools(self, tools_frame: ttk.Frame) -> None:
        """Add archive tool buttons and URL row to the tools area."""
        # Row 1: action buttons
        btn_row = ttk.Frame(tools_frame)
        btn_row.pack(fill=tk.X)

        self._archives_meta_direct_btn = ttk.Button(
            btn_row,
            text="extract .meta for downloads folder",
            state=tk.DISABLED,
            command=self._on_archive_meta_direct_click,
        )
        self._archives_meta_direct_btn.pack(side=tk.LEFT, padx=2, pady=2)

        self._archives_meta_btn = ttk.Button(
            btn_row,
            text="generate .meta for downloads folder (experimental)",
            state=tk.DISABLED,
            command=self._on_archive_meta_click,
        )
        self._archives_meta_btn.pack(side=tk.LEFT, padx=2, pady=2)

        # Row 2: URL display + open in browser
        url_row = ttk.Frame(tools_frame)
        url_row.pack(fill=tk.X, pady=(2, 0))

        ttk.Label(url_row, text="URL:").pack(side=tk.LEFT)
        self._archives_url_var = tk.StringVar()
        ttk.Entry(
            url_row,
            textvariable=self._archives_url_var,
            state="readonly",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self._archives_open_browser_btn = ttk.Button(
            url_row,
            text="open in browser",
            state=tk.DISABLED,
            command=self._on_archive_open_browser_click,
        )
        self._archives_open_browser_btn.pack(side=tk.LEFT, padx=2)
        self._archives_copy_url_btn = ttk.Button(
            url_row,
            text="copy",
            state=tk.DISABLED,
            command=self._on_archive_copy_url_click,
        )
        self._archives_copy_url_btn.pack(side=tk.LEFT, padx=(0, 2))

    def _get_archive_url(self, item: object) -> str | None:
        """Return a URL for the archive item, or None if not available.

        Priority:
        1. NexusDownloader with ModID + FileID → Nexus URL
        2. Any directURL= line produced by generate_meta (e.g. GoogleDriveDownloader, HttpDownloader…)
        3. State.Url (fallback for any other downloader)
        """
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
        # Extract directURL= from generated meta when available
        from ..wabba.generate_meta import generate_meta
        meta_content = generate_meta(item)
        if meta_content:
            for line in meta_content.splitlines():
                if line.startswith("directURL="):
                    return line[len("directURL="):]
        url = state.get("Url")
        if url:
            return url
        return None

    def _on_archive_open_browser_click(self) -> None:
        """Open the current archive URL in the system browser."""
        import webbrowser
        if self._archives_url_var is not None:
            url = self._archives_url_var.get()
            if url:
                webbrowser.open(url)

    def _on_archive_copy_url_click(self) -> None:
        """Copy the current archive URL to the clipboard."""
        if self._archives_url_var is not None:
            url = self._archives_url_var.get()
            if url:
                self.clipboard_clear()
                self.clipboard_append(url)

    def _on_archive_item_changed(self, item) -> None:
        """Enable or disable archive buttons based on current selection."""
        if self._archives_meta_btn is not None:
            from ..wabba.generate_meta import generate_meta
            has_generated_meta = isinstance(item, dict) and generate_meta(item) is not None
            self._archives_meta_btn.configure(
                state=tk.NORMAL if has_generated_meta else tk.DISABLED
            )
        if self._archives_meta_direct_btn is not None:
            state_obj = item.get("State") if isinstance(item, dict) else None
            state_type = state_obj.get("$type", "") if isinstance(state_obj, dict) else ""
            is_game_file = "GameFileSourceDownloader" in state_type
            has_meta = (
                not is_game_file
                and isinstance(item, dict)
                and isinstance(item.get("Meta"), str)
                and item["Meta"].strip() != ""
            )
            self._archives_meta_direct_btn.configure(
                state=tk.NORMAL if has_meta else tk.DISABLED
            )
        url = self._get_archive_url(item)
        if self._archives_url_var is not None:
            self._archives_url_var.set(url or "")
        for btn in (self._archives_open_browser_btn, self._archives_copy_url_btn):
            if btn is not None:
                btn.configure(state=tk.NORMAL if url else tk.DISABLED)

    def _on_archive_meta_click(self) -> None:
        """Save a generated .meta file for the selected archive."""
        from tkinter import filedialog, messagebox
        from ..wabba.generate_meta import generate_meta
        item = self._archives_panel.get_selected_item()
        if not isinstance(item, dict):
            return
        name = item.get("Name", "archive")
        base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        default_filename = base + ".meta"
        save_path = filedialog.asksaveasfilename(
            initialfile=default_filename,
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

    def _on_archive_meta_direct_click(self) -> None:
        """Save the archive's existing Meta field directly as a .meta file."""
        from tkinter import filedialog, messagebox
        item = self._archives_panel.get_selected_item()
        if not isinstance(item, dict):
            return
        meta = item.get("Meta", "")
        if not isinstance(meta, str) or not meta.strip():
            return
        name = item.get("Name", "archive")
        base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        default_filename = base + ".meta"
        save_path = filedialog.asksaveasfilename(
            initialfile=default_filename,
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

    def _archive_extra_info(self, archive_item: dict) -> str:
        """Return extra preview text for an archive entry.

        Includes:
        - directives that reference this archive
        - generated .meta content (with header)
        """
        from ..wabba.entry_info import get_archive_directives_text
        from ..wabba.generate_meta import generate_meta
        cache = self._wabba.cache if self._wabba else None
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
