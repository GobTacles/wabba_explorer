"""Mixin for the 'Archives' tab of WabbaExplorerApp."""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk

from .filtered_list_panel import _FilteredListPanel
from .gui_util import _archive_label, _item_matches, _archive_item_matches
from .gui_inline_edit import _do_remove_archive_and_directives


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
        remove_btn_ref: list = [None]
        remove_busy_ref: list = [False]
        find_in_downloads_btn_ref: list = [None]
        allow_edit = wabba is None

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
            directives_text = get_archive_directives_text(archive_item, directives, cache)
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

        # ── Downloads-analysis action helpers ─────────────────────────────

        def _run_downloads_operation_gui(mode: str, target_archive=None) -> None:
            """Common entry point for all three downloads operations."""
            from ..wabba.downloads_analysis import run_downloads_operation, save_report
            from ..wabba.downloads_analysis_fileops import make_log_filename
            from ..wabba.downloads_analysis_types import (
                DownloadsOperationRequest, MODE_FIND_ONE, MODE_MOVE_COPY, MODE_VERIFY,
            )
            from .gui_downloads_analysis_dialogs import (
                _DownloadsOptionsDialog, show_confirm_dialog, show_report_dialog, reveal_in_explorer,
            )
            from .gui_downloads_analysis_progress import DownloadsProgressDialog
            import os as _os

            w = _get_wabba()
            cache = w.cache if w is not None else None
            archives = cache.archives if cache is not None else None
            if not isinstance(archives, list):
                from tkinter import messagebox
                messagebox.showerror("Downloads", "No Archives are loaded.")
                return

            # Collect options via dialog
            dlg = _DownloadsOptionsDialog(self, mode)
            request = dlg.show()
            if request is None:
                return
            if target_archive is not None:
                request.target_archive = target_archive

            # Confirm callbacks that run on the GUI thread via threading.Event
            import threading as _threading

            def _make_confirm_cb(label: str):
                """Return a confirm callback safe to call from the worker thread."""
                def _cb(title: str, summary: str, details: str) -> bool:
                    event = _threading.Event()
                    result_holder = [False]

                    def _show() -> None:
                        result_holder[0] = show_confirm_dialog(self, title, summary, details)
                        event.set()

                    self.after(0, _show)
                    event.wait()
                    return result_holder[0]
                return _cb

            def _log_cb(line: str) -> None:
                print(f"[downloads] {line}")

            # Run in progress dialog
            progress_dlg = DownloadsProgressDialog(self)
            report = progress_dlg.run(
                request,
                archives,
                op_fn=run_downloads_operation,
                pre_hash_confirm_cb=_make_confirm_cb("pre-hash"),
                post_hash_confirm_cb=_make_confirm_cb("post-hash"),
                log_cb=_log_cb,
            )
            if report is None:
                return

            # Save report per mode
            save_path = ""
            if mode == MODE_MOVE_COPY and request.dest_folder:
                fname = make_log_filename("wabbaexplorer-move")
                save_path = _os.path.join(request.dest_folder, fname)
                try:
                    save_report(report, save_path)
                except Exception as exc:
                    print(f"[downloads] failed to save report: {exc}")
                    save_path = ""
            elif mode == MODE_VERIFY:
                from tkinter import filedialog
                save_path = filedialog.asksaveasfilename(
                    title="Save verify report",
                    defaultextension=".log",
                    filetypes=[("Log files", "*.log"), ("All files", "*.*")],
                    initialfile=make_log_filename("wabbaexplorer-verify"),
                )
                if save_path:
                    try:
                        save_report(report, save_path)
                    except Exception as exc:
                        print(f"[downloads] failed to save report: {exc}")
                        save_path = ""

            # Determine reveal path
            reveal_path = ""
            if mode == MODE_FIND_ONE:
                # Reveal the accepted candidate (or first any-candidate file)
                for hr in report.hash_results:
                    if hr.accepted_candidate:
                        reveal_path = hr.accepted_candidate.path
                        break
                if not reveal_path:
                    for mr in report.match_results:
                        if mr.candidates:
                            reveal_path = mr.candidates[0].path
                            break
            elif mode == MODE_MOVE_COPY:
                reveal_path = save_path  # reveal the saved log in dest folder
            elif mode == MODE_VERIFY and save_path:
                reveal_path = save_path

            report_text = "\n".join(report.log_lines)
            show_report_dialog(
                self,
                "Downloads operation report",
                report_text,
                reveal_path=reveal_path,
                save_path=save_path,
            )

        def _on_downloads_move_copy_click() -> None:
            from ..wabba.downloads_analysis_types import MODE_MOVE_COPY
            _run_downloads_operation_gui(MODE_MOVE_COPY)

        def _on_downloads_verify_click() -> None:
            from ..wabba.downloads_analysis_types import MODE_VERIFY
            _run_downloads_operation_gui(MODE_VERIFY)

        def _on_find_in_downloads_click() -> None:
            from ..wabba.downloads_analysis_types import MODE_FIND_ONE
            p = panel_ref[0]
            if p is None:
                return
            item = p.get_selected_item()
            if not isinstance(item, dict):
                return
            _run_downloads_operation_gui(MODE_FIND_ONE, target_archive=item)

        def _build_tools(tools_frame: ttk.Frame) -> None:
            btn_row_1 = ttk.Frame(tools_frame)
            btn_row_1.pack(fill=tk.X)

            meta_direct_btn = ttk.Button(
                btn_row_1,
                text="extract .meta for downloads folder",
                state=tk.DISABLED,
                command=_on_meta_direct_click,
            )
            meta_direct_btn.pack(side=tk.LEFT, padx=2, pady=2)
            meta_direct_btn_ref[0] = meta_direct_btn

            meta_btn = ttk.Button(
                btn_row_1,
                text="generate .meta for downloads folder",
                state=tk.DISABLED,
                command=_on_meta_click,
            )
            meta_btn.pack(side=tk.LEFT, padx=2, pady=2)
            meta_btn_ref[0] = meta_btn

            btn_row_2 = ttk.Frame(tools_frame)
            btn_row_2.pack(fill=tk.X)

            ttk.Button(
                btn_row_2,
                text="export all .meta for downloads",
                command=_on_meta_direct_all_click,
            ).pack(side=tk.LEFT, padx=2, pady=2)

            ttk.Button(
                btn_row_2,
                text="generate all .meta for downloads",
                command=_on_meta_all_click,
            ).pack(side=tk.LEFT, padx=2, pady=2)

            if allow_edit:
                remove_btn = ttk.Button(
                    btn_row_2,
                    text="remove archive and all directives using it",
                    state=tk.DISABLED,
                    command=_on_remove_archive_click,
                )
                remove_btn.pack(side=tk.LEFT, padx=2, pady=2)
                remove_btn_ref[0] = remove_btn

            btn_row_3 = ttk.Frame(tools_frame)
            btn_row_3.pack(fill=tk.X)

            ttk.Button(
                btn_row_3,
                text="move/copy from a shared downloads folder",
                command=_on_downloads_move_copy_click,
            ).pack(side=tk.LEFT, padx=2, pady=2)

            find_btn = ttk.Button(
                btn_row_3,
                text="find in downloads folder",
                state=tk.DISABLED,
                command=_on_find_in_downloads_click,
            )
            find_btn.pack(side=tk.LEFT, padx=2, pady=2)
            find_in_downloads_btn_ref[0] = find_btn

            ttk.Button(
                btn_row_3,
                text="verify (shared) downloads folder",
                command=_on_downloads_verify_click,
            ).pack(side=tk.LEFT, padx=2, pady=2)

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

            remove_btn = remove_btn_ref[0]
            if remove_btn is not None:
                if allow_edit and not remove_busy_ref[0] and isinstance(item, dict):
                    remove_btn.configure(state=tk.NORMAL)
                else:
                    remove_btn.configure(state=tk.DISABLED)

            find_btn = find_in_downloads_btn_ref[0]
            if find_btn is not None:
                state_obj = item.get("State") if isinstance(item, dict) else None
                state_type = state_obj.get("$type", "") if isinstance(state_obj, dict) else ""
                is_game_file = "GameFileSourceDownloader" in state_type
                find_btn.configure(
                    state=tk.NORMAL if isinstance(item, dict) and not is_game_file else tk.DISABLED
                )

        def _on_remove_busy_change(busy: bool) -> None:
            remove_busy_ref[0] = bool(busy)
            remove_btn = remove_btn_ref[0]
            if remove_btn is None:
                return
            if busy:
                remove_btn.configure(state=tk.DISABLED)
                return
            p = panel_ref[0]
            item = p.get_selected_item() if p is not None else None
            if allow_edit and isinstance(item, dict):
                remove_btn.configure(state=tk.NORMAL)
            else:
                remove_btn.configure(state=tk.DISABLED)

        def _on_remove_archive_click() -> None:
            p = panel_ref[0]
            if p is None:
                return
            item = p.get_selected_item()
            if not isinstance(item, dict):
                return
            w = _get_wabba()
            if w is None or w.cache is None:
                return
            _do_remove_archive_and_directives(
                item,
                w.cache.directives,
                on_queue_upsert=self._queue_inline_change,
                on_apply_now=self._apply_queued_changes_inplace,
                on_save_as_now=self._apply_queued_changes_save_as,
                on_busy_change=_on_remove_busy_change,
            )

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

        def _is_game_archive(item: object) -> bool:
            if not isinstance(item, dict):
                return False
            state_obj = item.get("State")
            state_type = state_obj.get("$type", "") if isinstance(state_obj, dict) else ""
            return "GameFileSourceDownloader" in state_type

        def _archive_meta_filename(item: dict) -> str:
            name = item.get("Name", "archive")
            base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            return base + ".meta"

        def _pick_empty_output_folder(*, action_title: str) -> str | None:
            from tkinter import filedialog, messagebox
            folder = filedialog.askdirectory(title=action_title)
            if not folder:
                return None
            try:
                entries = os.listdir(folder)
            except Exception as exc:
                messagebox.showerror(action_title, f"Failed to read folder:\n{exc}")
                return None
            if entries:
                messagebox.showerror(action_title, "Selected folder must be empty.")
                return None
            return folder

        def _print_bulk_meta_summary(
            *,
            action_title: str,
            folder: str,
            total: int,
            success: int,
            skipped: int,
            errors: list[str],
        ) -> None:
            print(
                f"[archives:{action_title}] folder={folder} total={total} "
                f"success={success} skipped={skipped} errors={len(errors)}"
            )
            for err in errors:
                print(f"[archives:{action_title}:error] {err}")

        def _show_bulk_meta_summary(
            *,
            action_title: str,
            folder: str,
            total: int,
            success: int,
            skipped: int,
            errors: list[str],
        ) -> None:
            from tkinter import messagebox

            summary_lines = [
                f"Folder: {folder}",
                f"Archives: {total}",
                f"Written: {success}",
                f"Skipped: {skipped}",
                f"Errors: {len(errors)}",
            ]
            if errors:
                summary_lines.append("")
                summary_lines.append("Errors:")
                preview = errors[:25]
                summary_lines.extend(f"- {msg}" for msg in preview)
                if len(errors) > len(preview):
                    summary_lines.append(f"- ... and {len(errors) - len(preview)} more")
                messagebox.showwarning(action_title, "\n".join(summary_lines))
            else:
                messagebox.showinfo(action_title, "\n".join(summary_lines))

        def _on_meta_direct_all_click() -> None:
            action_title = "export all .meta for downloads"
            w = _get_wabba()
            cache = w.cache if w is not None else None
            archives = cache.archives if cache is not None else None
            if not isinstance(archives, list):
                from tkinter import messagebox
                messagebox.showerror(action_title, "No Archives are loaded.")
                return

            folder = _pick_empty_output_folder(action_title=action_title)
            if not folder:
                return

            errors: list[str] = []
            success = 0
            skipped = 0
            total = len(archives)

            for item in archives:
                if not isinstance(item, dict):
                    errors.append("invalid archive entry type")
                    continue
                name = str(item.get("Name", "archive"))
                if _is_game_archive(item):
                    skipped += 1
                    continue

                meta = item.get("Meta", "")
                if not isinstance(meta, str) or not meta.strip():
                    errors.append(f"{name}: missing direct Meta content")
                    continue

                target_path = os.path.join(folder, _archive_meta_filename(item))
                if os.path.exists(target_path):
                    errors.append(f"{name}: target already exists, not overwriting ({target_path})")
                    continue

                try:
                    content = meta.replace("\\n", "\n")
                    if not content.endswith("\n"):
                        content += "\n"
                    with open(target_path, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(content)
                    success += 1
                except Exception as exc:
                    errors.append(f"{name}: write failed ({exc})")

            _print_bulk_meta_summary(
                action_title=action_title,
                folder=folder,
                total=total,
                success=success,
                skipped=skipped,
                errors=errors,
            )
            _show_bulk_meta_summary(
                action_title=action_title,
                folder=folder,
                total=total,
                success=success,
                skipped=skipped,
                errors=errors,
            )

        def _on_meta_all_click() -> None:
            from ..wabba.generate_meta import generate_meta
            from tkinter import messagebox

            action_title = "generate all .meta for downloads"
            w = _get_wabba()
            cache = w.cache if w is not None else None
            archives = cache.archives if cache is not None else None
            if not isinstance(archives, list):
                messagebox.showerror(action_title, "No Archives are loaded.")
                return

            add_installed = messagebox.askyesnocancel(
                action_title,
                "Add 'installed=true' line in .meta?",
            )
            if add_installed is None:
                return

            folder = _pick_empty_output_folder(action_title=action_title)
            if not folder:
                return

            errors: list[str] = []
            success = 0
            skipped = 0
            total = len(archives)

            for item in archives:
                if not isinstance(item, dict):
                    errors.append("invalid archive entry type")
                    continue
                name = str(item.get("Name", "archive"))
                if _is_game_archive(item):
                    skipped += 1
                    continue

                content = generate_meta(item, include_installed=bool(add_installed))
                if content is None:
                    errors.append(f"{name}: no generated Meta content")
                    continue

                target_path = os.path.join(folder, _archive_meta_filename(item))
                if os.path.exists(target_path):
                    errors.append(f"{name}: target already exists, not overwriting ({target_path})")
                    continue

                try:
                    with open(target_path, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(content)
                    success += 1
                except Exception as exc:
                    errors.append(f"{name}: write failed ({exc})")

            _print_bulk_meta_summary(
                action_title=action_title,
                folder=folder,
                total=total,
                success=success,
                skipped=skipped,
                errors=errors,
            )
            _show_bulk_meta_summary(
                action_title=action_title,
                folder=folder,
                total=total,
                success=success,
                skipped=skipped,
                errors=errors,
            )

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
