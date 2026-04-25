"""Cancellable progress dialog for the downloads-analysis workflow.

Shows: phase label, archive progress bar, elapsed timer.
The worker runs in a daemon background thread.
Cancel sets a flag that the engine checks between archives.
"""

from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..wabba.downloads_analysis_types import (
    DownloadsOperationReport,
    DownloadsOperationRequest,
    DownloadsProgressEvent,
)

# Messages the worker thread posts to the GUI thread via the queue.
_MSG_PROGRESS = "progress"
_MSG_DONE     = "done"


class DownloadsProgressDialog:
    """Modal progress window that runs the downloads operation in a thread.

    Usage::

        def run_op(request, archives, progress_cb, cancel_cb, ...):
            ...  # calls run_downloads_operation

        dlg = DownloadsProgressDialog(root)
        report = dlg.run(request, archives, op_fn=run_op, ...)
    """

    def __init__(self, root: tk.Misc) -> None:
        self._root = root
        self._cancelled = False
        self._queue: queue.Queue = queue.Queue()

    def is_cancelled(self) -> bool:
        return self._cancelled

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(
        self,
        request: DownloadsOperationRequest,
        archives: list[dict],
        *,
        op_fn: Callable,
        pre_hash_confirm_cb: "Callable | None" = None,
        post_hash_confirm_cb: "Callable | None" = None,
        log_cb: "Callable | None" = None,
    ) -> "DownloadsOperationReport | None":
        """Show the progress dialog and run the operation.

        Returns the DownloadsOperationReport when done (or None on error).
        Blocks until the operation finishes or is cancelled.
        """
        self._cancelled = False
        win = tk.Toplevel(self._root)
        win.title("Downloads operation – running…")
        win.resizable(False, False)
        win.grab_set()
        win.transient(self._root)
        win.protocol("WM_DELETE_WINDOW", lambda: None)   # prevent accidental close

        frame = ttk.Frame(win, padding=14)
        frame.pack(fill=tk.BOTH, expand=True)

        phase_var = tk.StringVar(value="Starting…")
        ttk.Label(frame, textvariable=phase_var, anchor=tk.W, width=60).pack(fill=tk.X)

        archive_var = tk.StringVar(value="")
        ttk.Label(
            frame, textvariable=archive_var, anchor=tk.W,
            font=("Consolas", 9), foreground="#555555",
        ).pack(fill=tk.X, pady=(2, 6))

        pb = ttk.Progressbar(frame, mode="determinate", maximum=1, length=520)
        pb.pack(fill=tk.X, pady=(0, 6))

        elapsed_var = tk.StringVar(value="Elapsed: 0s")
        ttk.Label(frame, textvariable=elapsed_var, anchor=tk.W).pack(fill=tk.X, pady=(0, 8))

        cancel_btn = ttk.Button(frame, text="Cancel", command=self._on_cancel, width=12)
        cancel_btn.pack(anchor=tk.E)

        self._win = win
        self._phase_var = phase_var
        self._archive_var = archive_var
        self._pb = pb
        self._elapsed_var = elapsed_var
        self._cancel_btn = cancel_btn
        self._t_start = time.monotonic()

        # Start elapsed timer
        self._schedule_elapsed_update()

        # Launch worker thread
        def _worker() -> None:
            try:
                report = op_fn(
                    request,
                    archives,
                    progress_cb=self._on_progress,
                    cancel_cb=self.is_cancelled,
                    log_cb=log_cb,
                    pre_hash_confirm_cb=pre_hash_confirm_cb,
                    post_hash_confirm_cb=post_hash_confirm_cb,
                )
            except Exception as exc:
                import traceback
                print(f"[downloads_analysis] worker exception: {exc}")
                traceback.print_exc()
                report = None
            self._queue.put((_MSG_DONE, report))

        threading.Thread(target=_worker, daemon=True).start()

        # Start queue polling
        self._result_holder: list = [None]
        self._poll_queue()

        # Block until dialog closes
        self._root.wait_window(win)
        return self._result_holder[0]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_cancel(self) -> None:
        self._cancelled = True
        try:
            self._cancel_btn.configure(state=tk.DISABLED, text="Cancelling…")
            self._phase_var.set("Cancelling…")
        except tk.TclError:
            pass

    def _on_progress(self, event: DownloadsProgressEvent) -> None:
        self._queue.put((_MSG_PROGRESS, event))

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == _MSG_PROGRESS:
                    self._apply_progress(payload)
                elif kind == _MSG_DONE:
                    self._result_holder[0] = payload
                    try:
                        self._win.destroy()
                    except tk.TclError:
                        pass
                    return
        except queue.Empty:
            pass
        try:
            self._win.after(80, self._poll_queue)
        except tk.TclError:
            pass

    def _apply_progress(self, event: DownloadsProgressEvent) -> None:
        try:
            self._phase_var.set(event.phase)
            if event.current_archive_name:
                self._archive_var.set(event.current_archive_name)
            if event.total > 0:
                if str(self._pb.cget("mode")) != "determinate":
                    self._pb.configure(mode="determinate")
                self._pb.configure(maximum=event.total, value=min(event.current, event.total))
            else:
                if str(self._pb.cget("mode")) != "indeterminate":
                    self._pb.configure(mode="indeterminate")
                    self._pb.start(12)
        except tk.TclError:
            pass

    def _schedule_elapsed_update(self) -> None:
        try:
            elapsed = time.monotonic() - self._t_start
            if elapsed < 60:
                text = f"Elapsed: {elapsed:.0f}s"
            else:
                m = int(elapsed // 60)
                s = int(elapsed % 60)
                text = f"Elapsed: {m}m {s}s"
            self._elapsed_var.set(text)
            self._win.after(1000, self._schedule_elapsed_update)
        except tk.TclError:
            pass
