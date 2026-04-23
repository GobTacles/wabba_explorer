"""Canvas-based virtual listbox that renders only visible rows on demand.

Works with any model that implements:
    __len__() -> int
    label_at(pos: int) -> str
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable


class _VirtualListbox(tk.Frame):
    """Listbox that only renders the rows visible in its viewport.

    Backed by a model object (e.g. :class:`VirtualListModel`).  When the
    model or its filtered set changes, call :meth:`refresh` to redraw.

    *on_select* – optional callback ``(pos: int) -> None`` where *pos* is the
    zero-based position within the *filtered* list.
    """

    ROW_H: int = 18          # pixels per row
    FONT = ("TkDefaultFont", 9)
    SEL_BG = "#316AC5"
    SEL_FG = "white"
    # Background colour – match native Listbox on every platform
    _CANVAS_BG: str = "white"

    def __init__(
        self,
        parent,
        on_select: Callable[[int], None] | None = None,
        **kwargs,
    ) -> None:
        # Outer frame acts as the border, just like a sunken Listbox frame
        super().__init__(parent, relief="sunken", bd=1, **kwargs)
        self._model = None
        self._selected: int = -1
        self._on_select = on_select
        self._render_pending: bool = False
        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        self._canvas = tk.Canvas(
            self,
            background=self._CANVAS_BG,
            highlightthickness=0,
            bd=0,
            yscrollincrement=self.ROW_H,
        )
        self._vsb = ttk.Scrollbar(self, command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._yscroll_cb)
        self._vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(fill=tk.BOTH, expand=True)

        self._canvas.bind("<Configure>", self._on_configure)
        self._canvas.bind("<ButtonRelease-1>", self._on_click)
        self._canvas.bind("<MouseWheel>", self._on_wheel)
        self._canvas.bind("<Button-4>", self._on_wheel)
        self._canvas.bind("<Button-5>", self._on_wheel)
        self._canvas.bind("<Up>", self._on_key_up)
        self._canvas.bind("<Down>", self._on_key_down)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_model(self, model) -> None:
        """Attach a new model and re-render from the top."""
        self._model = model
        self._selected = -1
        self._update_scrollregion()
        self._canvas.yview_moveto(0)
        self._schedule_render()

    def refresh(self) -> None:
        """Re-render after the model's filtered set or content has changed."""
        self._selected = -1
        self._update_scrollregion()
        self._canvas.yview_moveto(0)
        self._schedule_render()

    def get_selection(self) -> int:
        """Return selected position in filtered list, or -1."""
        return self._selected

    def focus(self) -> None:
        self._canvas.focus_set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _yscroll_cb(self, *args) -> None:
        self._vsb.set(*args)
        self._schedule_render()

    def _on_configure(self, _event=None) -> None:
        self._update_scrollregion()
        self._schedule_render()

    def _update_scrollregion(self) -> None:
        n = len(self._model) if self._model is not None else 0
        total_h = max(1, n) * self.ROW_H
        self._canvas.configure(scrollregion=(0, 0, 10000, total_h))

    def _schedule_render(self) -> None:
        if not self._render_pending:
            self._render_pending = True
            self._canvas.after_idle(self._do_render)

    def _do_render(self) -> None:
        self._render_pending = False
        c = self._canvas
        c.delete("all")

        model = self._model
        if model is None:
            return
        n = len(model)
        if not n:
            return

        ch = c.winfo_height() or 300
        cw = c.winfo_width() or 400

        top_px = c.canvasy(0)
        start_row = max(0, int(top_px // self.ROW_H))
        end_row = min(n, start_row + int(ch / self.ROW_H) + 2)

        row_h = self.ROW_H
        sel = self._selected
        sel_bg = self.SEL_BG
        sel_fg = self.SEL_FG
        font = self.FONT

        for row in range(start_row, end_row):
            y_top = row * row_h        # canvas coordinates, not window coordinates
            y_bot = y_top + row_h
            if row == sel:
                c.create_rectangle(0, y_top, cw, y_bot, fill=sel_bg, outline="")
                c.create_text(
                    4, y_top + 2, anchor="nw",
                    text=model.label_at(row), fill=sel_fg, font=font,
                )
            else:
                c.create_text(
                    4, y_top + 2, anchor="nw",
                    text=model.label_at(row), fill="black", font=font,
                )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _on_click(self, event) -> None:
        if self._model is None:
            return
        n = len(self._model)
        canvas_y = self._canvas.canvasy(event.y)
        row = int(canvas_y // self.ROW_H)
        if 0 <= row < n:
            self._selected = row
            self._canvas.focus_set()
            self._schedule_render()
            if self._on_select:
                self._on_select(row)

    def _on_wheel(self, event) -> None:
        if event.num == 4:
            self._canvas.yview_scroll(-3, "units")
        elif event.num == 5:
            self._canvas.yview_scroll(3, "units")
        elif hasattr(event, "delta") and event.delta:
            self._canvas.yview_scroll(int(-event.delta / 40), "units")

    def _on_key_up(self, _event=None) -> None:
        if self._model is None or len(self._model) == 0:
            return
        new_sel = max(0, self._selected - 1) if self._selected > 0 else 0
        self._selected = new_sel
        self._ensure_visible(new_sel)
        self._schedule_render()
        if self._on_select:
            self._on_select(new_sel)

    def _on_key_down(self, _event=None) -> None:
        if self._model is None:
            return
        n = len(self._model)
        if n == 0:
            return
        new_sel = min(n - 1, self._selected + 1) if self._selected >= 0 else 0
        self._selected = new_sel
        self._ensure_visible(new_sel)
        self._schedule_render()
        if self._on_select:
            self._on_select(new_sel)

    def _ensure_visible(self, row: int) -> None:
        if self._model is None:
            return
        n = len(self._model)
        if n == 0:
            return
        ch = self._canvas.winfo_height() or 300
        visible_rows = max(1, int(ch / self.ROW_H))
        top_px = self._canvas.canvasy(0)
        top_row = int(top_px // self.ROW_H)
        if row < top_row:
            self._canvas.yview_moveto(row / n)
        elif row >= top_row + visible_rows:
            self._canvas.yview_moveto((row - visible_rows + 1) / n)
