"""GUI-independent model for a filtered virtual list.

Stores pre-computed label strings and the current filtered subset (as a list
of indices into the full labels/items arrays).  Filtering is a pure Python
operation with no I/O or GUI calls, safe to call on the main thread after a
filter-change event without blocking the UI noticeably even for 300 k entries.
"""

from __future__ import annotations

import re
from typing import Callable


class VirtualListModel:
    """Holds items, pre-computed label strings, and a filtered index list.

    Typical usage::

        model = VirtualListModel()
        model.set_data(cache.directives, cache.directive_labels)
        model.apply_filter(pattern, item_gate)
        # GUI reads len(model), model.label_at(pos), model.item_at(pos)
    """

    __slots__ = ("items", "labels", "filtered_indices")

    def __init__(self) -> None:
        self.items: list = []
        self.labels: list[str] = []
        self.filtered_indices: list[int] = []

    def set_data(self, items: list, labels: list[str]) -> None:
        """Replace all data and reset filter to show all items."""
        self.items = items
        self.labels = labels
        self.filtered_indices = list(range(len(items)))

    def apply_filter(
        self,
        pattern: re.Pattern | None,
        item_gate: Callable[[object], bool] | None = None,
    ) -> None:
        """Recompute *filtered_indices* in-place.

        *pattern*   – compiled regex to match against label strings, or None.
        *item_gate* – optional extra boolean gate (e.g. type-checkbox filter).
        """
        labels = self.labels
        items = self.items
        n = len(labels)

        if pattern is None and item_gate is None:
            self.filtered_indices = list(range(n))
            return

        result: list[int] = []
        if pattern is not None and item_gate is not None:
            pat_search = pattern.search
            for i in range(n):
                if item_gate(items[i]) and pat_search(labels[i]) is not None:
                    result.append(i)
        elif pattern is not None:
            pat_search = pattern.search
            for i in range(n):
                if pat_search(labels[i]) is not None:
                    result.append(i)
        else:
            for i in range(n):
                if item_gate(items[i]):  # type: ignore[misc]
                    result.append(i)
        self.filtered_indices = result

    def __len__(self) -> int:
        return len(self.filtered_indices)

    def label_at(self, pos: int) -> str:
        return self.labels[self.filtered_indices[pos]]

    def item_at(self, pos: int) -> object:
        return self.items[self.filtered_indices[pos]]
