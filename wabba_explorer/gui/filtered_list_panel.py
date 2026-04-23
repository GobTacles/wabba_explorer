"""Reusable filtered-list + preview panel (tkinter)."""

import json
import time
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .gui_util import _build_name_pattern
from .tooltip import _Tooltip
from .virtual_listbox import _VirtualListbox
from ..wabba.virtual_list_model import VirtualListModel

_FILTER_DEBOUNCE_MS = 300


# ---------------------------------------------------------------------------
# Minimal model wrapper used only for the "Loading…" placeholder state.
# ---------------------------------------------------------------------------

class _PlaceholderModel:
    """Single-item dummy model shown while real data is still loading."""

    def __init__(self, text: str = "Loading\u2026") -> None:
        self._text = text

    def __len__(self) -> int:
        return 1

    def label_at(self, _pos: int) -> str:
        return self._text


# ---------------------------------------------------------------------------


class _FilteredListPanel(ttk.Frame):
    """Horizontal paned widget: (virtual list + filter entry) | text preview.

    Supports two loading paths:

    * :meth:`load_model` – fast path with a :class:`VirtualListModel` whose
      label strings were pre-computed in a background thread.
    * :meth:`load_items` – compatibility path that builds a model on the fly
      using *label_fn*.

    *label_fn*          – callable(item) -> str   (used only by load_items)
    *filter_fn*         – unused (kept for API compat; filtering is done on
                           pre-computed label strings via the model)
    *extra_info_fn*     – callable(item) -> str   extra text in preview pane
    *extra_controls_fn* – callable(left_frame)    adds extra widgets to the
                           left column (e.g. type-checkbox bar)
    *item_filter_fn*    – callable(item) -> bool  additional gate applied
                           alongside the text filter (e.g. type checkboxes)
    """

    def __init__(
        self,
        parent,
        label_fn: Callable,
        filter_fn: Callable | None = None,
        extra_info_fn: Callable | None = None,
        extra_controls_fn: Callable | None = None,
        item_filter_fn: Callable | None = None,
        tools_fn: Callable | None = None,
        on_item_changed: Callable | None = None,
        **kwargs,
    ) -> None:
        super().__init__(parent, **kwargs)
        self._label_fn = label_fn
        self._extra_info_fn = extra_info_fn
        self._extra_controls_fn = extra_controls_fn
        self._item_filter_fn = item_filter_fn
        self._tools_fn = tools_fn
        self._on_item_changed = on_item_changed
        self._model: VirtualListModel | None = None
        self._filter_job: str | None = None
        self._current_item = None
        self._build()

    def _build(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        # --- left side: virtual listbox + filter bar ---
        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        self._vlist = _VirtualListbox(left, on_select=self._on_vlist_select)
        self._vlist.pack(fill=tk.BOTH, expand=True)
        # Show loading placeholder immediately
        self._vlist.set_model(_PlaceholderModel())

        filter_bar = ttk.Frame(left)
        filter_bar.pack(fill=tk.X, pady=(2, 0))
        ttk.Label(filter_bar, text="Filter:").pack(side=tk.LEFT)
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", self._on_filter_change)
        _PLACEHOLDER = "^=start, *=wildcard"
        self._filter_placeholder = _PLACEHOLDER
        self._filter_count_var = tk.StringVar(value="")
        ttk.Label(filter_bar, textvariable=self._filter_count_var).pack(side=tk.RIGHT, padx=(4, 0))
        self._filter_entry = tk.Entry(filter_bar, foreground="gray")
        self._filter_entry.insert(0, _PLACEHOLDER)
        self._filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _Tooltip(
            self._filter_entry,
            "^=anchor to start, *=any characters\nExample: ^Begin*Middle",
        )
        ttk.Button(filter_bar, text="×", width=2, command=self._clear_filter).pack(side=tk.LEFT, padx=(2, 0))

        _ph_active = [True]  # True while the placeholder is showing
        self._ph_active = _ph_active

        def _on_focus_in(event, _entry=self._filter_entry, _state=_ph_active) -> None:
            if _state[0]:
                _state[0] = False
                _entry.configure(foreground="black")
                _entry.delete(0, tk.END)

        def _on_focus_out(
            event,
            _entry=self._filter_entry,
            _var=self._filter_var,
            _state=_ph_active,
            _ph=_PLACEHOLDER,
        ) -> None:
            if not _entry.get():
                _state[0] = True
                _var.set("")
                _entry.configure(foreground="gray")
                _entry.delete(0, tk.END)
                _entry.insert(0, _ph)

        def _on_key_release(
            event,
            _entry=self._filter_entry,
            _var=self._filter_var,
            _state=_ph_active,
        ) -> None:
            if not _state[0]:
                new_val = _entry.get()
                if _var.get() != new_val:
                    _var.set(new_val)

        self._filter_entry.bind("<FocusIn>", _on_focus_in)
        self._filter_entry.bind("<FocusOut>", _on_focus_out)
        self._filter_entry.bind("<KeyRelease>", _on_key_release)

        if self._extra_controls_fn is not None:
            self._extra_controls_fn(left)

        # --- right side: preview text + tools area ---
        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        # Tools area is packed first (BOTTOM) so it stays visible when resized
        tools_frame = ttk.Frame(right)
        tools_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(2, 0))
        if self._tools_fn is not None:
            self._tools_fn(tools_frame)

        self._preview = tk.Text(
            right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        sb2 = ttk.Scrollbar(right, command=self._preview.yview)
        self._preview.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self._preview.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_loading(self) -> None:
        """Show a 'Loading...' placeholder while data is being fetched."""
        self._model = None
        self._current_item = None
        if self._on_item_changed is not None:
            self._on_item_changed(None)
        self._vlist.set_model(_PlaceholderModel())
        self._filter_count_var.set("")
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert(tk.END, "Loading\u2026")
        self._preview.configure(state=tk.DISABLED)

    def load_model(self, model: VirtualListModel) -> None:
        """Fast path: attach a pre-built VirtualListModel and display it.

        Applies any current filter text and item_filter_fn before rendering.
        """
        self._model = model
        self._apply_filter(self._filter_var.get())

    def load_items(self, items: list) -> None:
        """Compatibility path: build a model from *items* using *label_fn*."""
        labels = [self._label_fn(item) for item in items]
        model = VirtualListModel()
        model.set_data(items, labels)
        self.load_model(model)

    def refresh_filter(self) -> None:
        """Re-apply the current filter (e.g. after an item_filter_fn change)."""
        self._apply_filter(self._filter_var.get())

    def get_selected_item(self):
        """Return the currently selected item, or None."""
        return self._current_item

    # ------------------------------------------------------------------
    # Filter logic
    # ------------------------------------------------------------------

    def _on_filter_change(self, *_) -> None:
        if self._filter_job is not None:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(_FILTER_DEBOUNCE_MS, self._do_filter)

    def _do_filter(self) -> None:
        self._filter_job = None
        self._apply_filter(self._filter_var.get())

    def _clear_filter(self) -> None:
        """Clear the filter entry and reset to placeholder state."""
        self._ph_active[0] = True
        self._filter_var.set("")
        self._filter_entry.configure(foreground="gray")
        self._filter_entry.delete(0, tk.END)
        self._filter_entry.insert(0, self._filter_placeholder)
        self._apply_filter("")

    def _apply_filter(self, text: str) -> None:
        if self._model is None:
            return
        total_before = len(self._model)
        t0 = time.monotonic()
        if text:
            pattern = _build_name_pattern(text)
            if pattern is None:
                # Invalid filter pattern - show nothing
                self._model.filtered_indices = []
                self._vlist.set_model(self._model)
                self._filter_count_var.set("0 entries")
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                print(f"[filter] '{text}'  {total_before} -> 0  ({elapsed_ms} ms)")
                return
        else:
            pattern = None
        self._model.apply_filter(pattern, self._item_filter_fn)
        self._vlist.set_model(self._model)
        total_after = len(self._model)
        self._filter_count_var.set(f"{total_after} entries")
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        if text:
            print(f"[filter] '{text}'  {total_before} -> {total_after}  ({elapsed_ms} ms)")

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_vlist_select(self, pos: int) -> None:
        """Called by _VirtualListbox when the user selects row *pos*."""
        if self._model is None:
            return
        if pos < 0 or pos >= len(self._model):
            return
        item = self._model.item_at(pos)
        if item is None:
            return
        self._current_item = item
        if self._on_item_changed is not None:
            self._on_item_changed(item)
        text = _truncate_preview(json.dumps(item, indent=2))
        if self._extra_info_fn is not None:
            extra = self._extra_info_fn(item)
            if extra:
                text = text + "\n\n" + extra
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert(tk.END, text)
        self._preview.configure(state=tk.DISABLED)


def _truncate_preview(s: str, max_chars: int = 4096) -> str:
    if len(s) > max_chars:
        return s[:max_chars] + f"\n\u2026 (truncated, {len(s)} chars total)"
    return s
