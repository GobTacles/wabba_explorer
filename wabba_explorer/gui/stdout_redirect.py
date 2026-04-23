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

    Each line is prefixed with ``HH:MM:SS.sss `` at the point where it
    starts (i.e. after every newline, or at the very beginning when we are
    at the start of a new line).  The timestamp is captured when ``write``
    is called so that background-thread messages retain their original
    timing.
    """

    def __init__(self, text_widget: tk.Text, original) -> None:
        self._widget = text_widget
        self._original = original
        self._main_thread_id = threading.main_thread().ident
        self._lock = threading.Lock()
        self._at_line_start = True  # True ⟹ next non-empty char starts a new line

    # ------------------------------------------------------------------

    @staticmethod
    def _now_prefix() -> str:
        now = datetime.datetime.now()
        return now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d} "

    def _format(self, s: str) -> str:
        """Return *s* with ``HH:MM:SS.sss `` inserted at every line start.

        Mutates ``self._at_line_start`` as a side-effect.
        Must be called while holding ``self._lock``.
        """
        if not s:
            return s
        result: list[str] = []
        i = 0
        while i < len(s):
            nl = s.find("\n", i)
            if nl == -1:
                # No more newlines – rest is a partial line
                chunk = s[i:]
                if self._at_line_start and chunk:
                    result.append(self._now_prefix())
                    self._at_line_start = False
                result.append(chunk)
                break
            # Characters up to the newline, then the newline itself
            chunk = s[i:nl]
            if self._at_line_start and chunk:
                result.append(self._now_prefix())
            result.append(chunk)
            result.append("\n")
            self._at_line_start = True
            i = nl + 1
        return "".join(result)

    def write(self, s: str) -> int:
        with self._lock:
            formatted = self._format(s)
        if threading.current_thread().ident == self._main_thread_id:
            self._write_to_widget(formatted)
        else:
            self._widget.after(0, self._write_to_widget, formatted)
        if self._original is not None and hasattr(self._original, "write"):
            self._original.write(s)
        return len(s)

    def _write_to_widget(self, s: str) -> None:
        self._widget.configure(state=tk.NORMAL)
        self._widget.insert(tk.END, s)
        self._widget.see(tk.END)
        self._widget.configure(state=tk.DISABLED)

    def flush(self) -> None:
        if self._original is not None and hasattr(self._original, "flush"):
            self._original.flush()
