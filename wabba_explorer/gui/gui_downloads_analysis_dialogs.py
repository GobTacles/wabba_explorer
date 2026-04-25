"""GUI dialogs for the downloads-analysis workflow.

Contains:
- _DownloadsOptionsDialog  – initial options dialog (folder, actions)
- _ConfirmDialog           – pre-hash and post-hash confirmation dialogs
- _ReportDialog            – final report with reveal-in-explorer button
- reveal_in_explorer()     – OS-level helper to highlight a file in the explorer
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..wabba.downloads_analysis_types import (
    ARCHIVE_ACTION_COPY,
    ARCHIVE_ACTION_MOVE,
    META_ACTION_COPY,
    META_ACTION_EXPORT,
    META_ACTION_GENERATE,
    META_ACTION_MOVE,
    META_ACTION_SKIP,
    META_FALLBACK_EXPORT,
    META_FALLBACK_GENERATE,
    META_FALLBACK_SKIP,
    MODE_FIND_ONE,
    MODE_MOVE_COPY,
    MODE_VERIFY,
    DownloadsOperationRequest,
)


# ---------------------------------------------------------------------------
# Reveal in file explorer
# ---------------------------------------------------------------------------

def reveal_in_explorer(path: str) -> None:
    """Open the OS file explorer at *path*, highlighting the file.

    Falls back to opening the containing folder when the OS does not support
    file-selection (non-Windows).
    """
    if not path:
        return
    path = os.path.abspath(path)
    if sys.platform == "win32":
        # Explorer /select,<path>  highlights the file in Windows Explorer.
        subprocess.Popen(["explorer", "/select,", path])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        # xdg-open the parent folder as a best-effort fallback
        folder = os.path.dirname(path) if os.path.isfile(path) else path
        subprocess.Popen(["xdg-open", folder])


# ---------------------------------------------------------------------------
# Options dialog
# ---------------------------------------------------------------------------

class _DownloadsOptionsDialog:
    """Modal dialog that collects all parameters for a downloads operation.

    Call ``show()`` to run the dialog.  Returns the populated
    ``DownloadsOperationRequest`` or ``None`` if the user cancelled.
    """

    def __init__(self, root: tk.Misc, mode: str) -> None:
        self._root = root
        self._mode = mode
        self._result: "DownloadsOperationRequest | None" = None

    def show(self) -> "DownloadsOperationRequest | None":
        mode = self._mode
        if mode == MODE_MOVE_COPY:
            title = "Move/Copy from shared downloads folder"
        elif mode == MODE_VERIFY:
            title = "Verify shared downloads folder"
        else:
            title = "Find in downloads folder"

        win = tk.Toplevel(self._root)
        win.title(title)
        win.resizable(False, False)
        win.grab_set()
        win.transient(self._root)

        frame = ttk.Frame(win, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)

        row = 0

        # ── Shared downloads folder ──────────────────────────────────
        ttk.Label(frame, text="Shared downloads folder:").grid(
            row=row, column=0, sticky=tk.W, pady=(0, 2)
        )
        row += 1
        shared_var = tk.StringVar()
        shared_entry = ttk.Entry(frame, textvariable=shared_var, width=60)
        shared_entry.grid(row=row, column=0, sticky=tk.EW, padx=(0, 4))
        ttk.Button(
            frame, text="Browse…",
            command=lambda: shared_var.set(
                filedialog.askdirectory(title="Select shared downloads folder") or shared_var.get()
            ),
        ).grid(row=row, column=1)
        row += 1

        # ── Destination folder (move/copy mode only) ─────────────────
        dest_var = tk.StringVar()
        if mode == MODE_MOVE_COPY:
            ttk.Label(frame, text="Destination folder (must be empty):").grid(
                row=row, column=0, sticky=tk.W, pady=(8, 2)
            )
            row += 1
            dest_entry = ttk.Entry(frame, textvariable=dest_var, width=60)
            dest_entry.grid(row=row, column=0, sticky=tk.EW, padx=(0, 4))
            ttk.Button(
                frame, text="Browse…",
                command=lambda: dest_var.set(
                    filedialog.askdirectory(title="Select destination folder (must be empty)") or dest_var.get()
                ),
            ).grid(row=row, column=1)
            row += 1

        # ── Archive action (move/copy mode only) ─────────────────────
        archive_action_var = tk.StringVar(value=ARCHIVE_ACTION_COPY)
        if mode == MODE_MOVE_COPY:
            ttk.Label(frame, text="Archive action:").grid(
                row=row, column=0, sticky=tk.W, pady=(8, 2)
            )
            row += 1
            arc_row = ttk.Frame(frame)
            arc_row.grid(row=row, column=0, columnspan=2, sticky=tk.W)
            ttk.Radiobutton(
                arc_row, text="Copy", variable=archive_action_var, value=ARCHIVE_ACTION_COPY
            ).pack(side=tk.LEFT, padx=(0, 8))
            ttk.Radiobutton(
                arc_row, text="Move", variable=archive_action_var, value=ARCHIVE_ACTION_MOVE
            ).pack(side=tk.LEFT)
            row += 1

        # ── Meta action (move/copy mode only) ────────────────────────
        meta_action_var = tk.StringVar(value=META_ACTION_SKIP)
        meta_fallback_var = tk.StringVar(value=META_FALLBACK_SKIP)
        fallback_widgets: list[tk.Widget] = []

        def _update_fallback_state(*_) -> None:
            state = tk.NORMAL if meta_action_var.get() in (META_ACTION_MOVE, META_ACTION_COPY) else tk.DISABLED
            for w in fallback_widgets:
                try:
                    w.configure(state=state)
                except tk.TclError:
                    pass

        if mode == MODE_MOVE_COPY:
            ttk.Label(frame, text=".meta action:").grid(
                row=row, column=0, sticky=tk.W, pady=(8, 2)
            )
            row += 1
            meta_row = ttk.Frame(frame)
            meta_row.grid(row=row, column=0, columnspan=2, sticky=tk.W)
            for val, lbl in [
                (META_ACTION_MOVE,     "Move"),
                (META_ACTION_COPY,     "Copy"),
                (META_ACTION_EXPORT,   "Export"),
                (META_ACTION_GENERATE, "Generate"),
                (META_ACTION_SKIP,     "Skip"),
            ]:
                ttk.Radiobutton(
                    meta_row, text=lbl,
                    variable=meta_action_var, value=val,
                    command=_update_fallback_state,
                ).pack(side=tk.LEFT, padx=(0, 8))
            row += 1

            ttk.Label(frame, text="Missing .meta fallback:").grid(
                row=row, column=0, sticky=tk.W, pady=(4, 2)
            )
            row += 1
            fb_row = ttk.Frame(frame)
            fb_row.grid(row=row, column=0, columnspan=2, sticky=tk.W)
            for val, lbl in [
                (META_FALLBACK_EXPORT,   "Export"),
                (META_FALLBACK_GENERATE, "Generate"),
                (META_FALLBACK_SKIP,     "Skip"),
            ]:
                rb = ttk.Radiobutton(
                    fb_row, text=lbl,
                    variable=meta_fallback_var, value=val,
                    state=tk.DISABLED,
                )
                rb.pack(side=tk.LEFT, padx=(0, 8))
                fallback_widgets.append(rb)
            row += 1

        # ── OK / Cancel ──────────────────────────────────────────────
        btn_row = ttk.Frame(frame)
        btn_row.grid(row=row, column=0, columnspan=2, pady=(12, 0), sticky=tk.E)

        result_holder: list = [None]

        def _on_ok() -> None:
            shared = shared_var.get().strip()
            if not shared or not os.path.isdir(shared):
                messagebox.showerror(
                    title, "Shared downloads folder does not exist or is not selected.",
                    parent=win,
                )
                return

            dest = dest_var.get().strip()
            if mode == MODE_MOVE_COPY:
                if not dest:
                    messagebox.showerror(title, "Please select a destination folder.", parent=win)
                    return
                if not os.path.isdir(dest):
                    messagebox.showerror(title, "Destination folder does not exist.", parent=win)
                    return
                try:
                    entries = os.listdir(dest)
                except Exception as exc:
                    messagebox.showerror(title, f"Cannot read destination folder:\n{exc}", parent=win)
                    return
                if entries:
                    messagebox.showerror(
                        title, "Destination folder must be empty.", parent=win
                    )
                    return

            result_holder[0] = DownloadsOperationRequest(
                mode=mode,
                shared_folder=shared,
                dest_folder=dest,
                archive_action=archive_action_var.get(),
                meta_action=meta_action_var.get(),
                meta_fallback=meta_fallback_var.get(),
            )
            win.destroy()

        def _on_cancel() -> None:
            win.destroy()

        ttk.Button(btn_row, text="OK", command=_on_ok, width=10).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="Cancel", command=_on_cancel, width=10).pack(side=tk.LEFT)

        frame.columnconfigure(0, weight=1)
        win.protocol("WM_DELETE_WINDOW", _on_cancel)
        self._root.wait_window(win)
        return result_holder[0]


# ---------------------------------------------------------------------------
# Confirm dialog (pre-hash / post-hash)
# ---------------------------------------------------------------------------

def show_confirm_dialog(
    root: tk.Misc,
    title: str,
    summary: str,
    details: str,
) -> bool:
    """Show a modal confirm dialog with a scrollable details area.

    Returns True if the user clicked "Continue", False otherwise.
    """
    result_holder = [False]

    win = tk.Toplevel(root)
    win.title(title)
    win.resizable(True, True)
    win.geometry("640x420")
    win.grab_set()
    win.transient(root)

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    ttk.Label(frame, text=summary, anchor=tk.W, font=("TkDefaultFont", 10, "bold")).pack(
        fill=tk.X, pady=(0, 6)
    )

    text = tk.Text(frame, wrap=tk.WORD, state=tk.NORMAL, font=("Consolas", 9))
    sb = ttk.Scrollbar(frame, command=text.yview)
    text.configure(yscrollcommand=sb.set)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(fill=tk.BOTH, expand=True)
    text.insert(tk.END, details)
    text.configure(state=tk.DISABLED)

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill=tk.X, pady=(8, 0))

    def _on_continue() -> None:
        result_holder[0] = True
        win.destroy()

    def _on_abort() -> None:
        win.destroy()

    ttk.Button(btn_row, text="Continue", command=_on_continue, width=12).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(btn_row, text="Abort", command=_on_abort, width=12).pack(side=tk.LEFT)

    win.protocol("WM_DELETE_WINDOW", _on_abort)
    root.wait_window(win)
    return result_holder[0]


# ---------------------------------------------------------------------------
# Report dialog
# ---------------------------------------------------------------------------

def show_report_dialog(
    root: tk.Misc,
    title: str,
    report_text: str,
    *,
    reveal_path: str = "",
    save_path: str = "",
) -> None:
    """Show the final operation report in a scrollable dialog.

    *reveal_path* – if set, a "Reveal in file explorer" button is shown
    that opens the OS explorer and highlights the file.
    *save_path*   – if set, shown in the dialog header as the saved log path.
    """
    win = tk.Toplevel(root)
    win.title(title)
    win.resizable(True, True)
    win.geometry("760x520")
    win.grab_set()
    win.transient(root)

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    if save_path:
        ttk.Label(
            frame,
            text=f"Report saved to: {save_path}",
            anchor=tk.W,
            font=("TkDefaultFont", 9),
        ).pack(fill=tk.X, pady=(0, 4))

    text = tk.Text(frame, wrap=tk.WORD, state=tk.NORMAL, font=("Consolas", 9))
    sb = ttk.Scrollbar(frame, command=text.yview)
    text.configure(yscrollcommand=sb.set)
    sb.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(fill=tk.BOTH, expand=True)
    text.insert(tk.END, report_text)
    text.configure(state=tk.DISABLED)

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill=tk.X, pady=(8, 0))

    if reveal_path:
        _reveal_target = reveal_path

        def _on_reveal() -> None:
            reveal_in_explorer(_reveal_target)

        ttk.Button(btn_row, text="Reveal in file explorer", command=_on_reveal).pack(
            side=tk.LEFT, padx=(0, 6)
        )

    ttk.Button(btn_row, text="Close", command=win.destroy, width=10).pack(side=tk.LEFT)

    win.protocol("WM_DELETE_WINDOW", win.destroy)
