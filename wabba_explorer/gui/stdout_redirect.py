"""Stdout/stderr redirect to a tkinter Text widget."""

import datetime
import io
import threading
import tkinter as tk


class _StdoutRedirect(io.TextIOBase):
    """Tee stdout to a tkinter Text widget (readonly) and the real stdout.

    Thread-safe: calls from non-main threads are marshalled to the main
    thread via ``widget.after(0, ...)`` so Tkinter is never touched from
    a background thread.

    Each line is prefixed with ``HH:MM:SS.sss `` at the start.  The
    timestamp is captured at the moment the complete line is assembled so
    that background-thread messages retain their original timing.

    Python's ``print()`` issues two separate ``write()`` calls — one for
    the text and one for the trailing ``"\\n"``.  A plain per-write lock
    would still let another thread slip in between those two calls and
    produce merged console lines.  To prevent this, each thread buffers
    its own partial output; only complete lines (ending with ``\\n``) are
    emitted to the widget, making each line appear atomically.  No shared
    lock is needed: per-thread buffers eliminate shared mutable state, and
    CPython's GIL serialises ``widget.after()`` enqueues naturally.
    """

    def __init__(self, text_widget: tk.Text, original) -> None:
        self._widget = text_widget
        self._original = original
        self._main_thread_id = threading.main_thread().ident
        # Per-thread state: 'buf' accumulates the current partial line.
        self._tls = threading.local()

    # ------------------------------------------------------------------

    @staticmethod
    def _now_prefix() -> str:
        now = datetime.datetime.now()
        return now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d} "

    def _emit(self, s: str) -> None:
        """Send already-formatted text to the widget (and real stdout)."""
        if threading.current_thread().ident == self._main_thread_id:
            self._write_to_widget(s)
        else:
            self._widget.after(0, self._write_to_widget, s)

    def write(self, s: str) -> int:
        # Accumulate into per-thread buffer; emit only complete lines so
        # that the text and its trailing "\n" are never separated by output
        # from another thread.  Both the real terminal and the GUI widget
        # receive the same timestamped text.
        buf: str = getattr(self._tls, "buf", "") + s
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            formatted = self._now_prefix() + line + "\n"
            if self._original is not None and hasattr(self._original, "write"):
                self._original.write(formatted)
            self._emit(formatted)
        self._tls.buf = buf
        return len(s)

    def flush(self) -> None:
        # Emit any buffered partial line that has no trailing newline yet.
        buf: str = getattr(self._tls, "buf", "")
        if buf:
            formatted = self._now_prefix() + buf
            self._tls.buf = ""
            if self._original is not None and hasattr(self._original, "write"):
                self._original.write(formatted)
            self._emit(formatted)
        if self._original is not None and hasattr(self._original, "flush"):
            self._original.flush()

    def _write_to_widget(self, s: str) -> None:
        self._widget.configure(state=tk.NORMAL)
        self._widget.insert(tk.END, s)
        self._widget.see(tk.END)
        self._widget.configure(state=tk.DISABLED)
