"""Simple hover tooltip for tkinter widgets."""

import tkinter as tk


class _Tooltip:
    """Simple hover tooltip for any tkinter widget."""

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget = widget
        self._text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def _show(self, event=None) -> None:
        if self._tip is not None:
            return
        x = self._widget.winfo_rootx() + 20
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 2
        self._tip = tk.Toplevel(self._widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(
            self._tip,
            text=self._text,
            justify=tk.LEFT,
            background="#ffffe0",
            relief=tk.SOLID,
            borderwidth=1,
            font=("TkDefaultFont", 8),
        )
        lbl.pack(ipadx=4, ipady=2)

    def _hide(self, event=None) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
