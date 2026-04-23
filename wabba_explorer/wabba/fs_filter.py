"""Pure filtering logic for the Files tab tree.

:func:`compute_filtered_paths` is the GUI-agnostic core of
``_FsTreePanel._apply_filter``.  Given a compiled regex pattern, a
directive-type bitmask, and a populated ``WabbaCache`` it returns the set
of paths that should be visible in the tree together with the file-only
match count.  Ancestor folders of matching files are automatically included
in the visible set.

The function has no tkinter dependency and can be unit-tested independently
of the GUI.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache import WabbaCache

from .cache import FS_FLAG_ALL


def compute_filtered_paths(
    pattern: "re.Pattern | None",
    type_mask: int,
    cache: "WabbaCache",
) -> tuple[set[str], int]:
    """Return ``(visible_path_set, match_count)`` for the given filter.

    Parameters
    ----------
    pattern:
        Pre-compiled regex to match against the basename of each file path,
        or ``None`` for no text filter.
    type_mask:
        OR of ``FS_FLAG_*`` constants from :mod:`wabba.cache`.
        ``FS_FLAG_ALL`` (15) means no type filtering.
    cache:
        Populated ``WabbaCache`` (``files_ready`` should be set, but the
        function degrades gracefully if not).

    Returns
    -------
    tuple[set[str], int]
        * ``visible`` – set of all path strings that should appear in the
          tree (matching leaf files **plus** all their ancestor folder paths).
        * ``match_count`` – number of leaf *files* that matched (folders are
          not counted).
    """
    fp = cache.fs_folder_paths or set()
    sorted_paths = cache.fs_sorted_paths or []
    type_filter_active = type_mask != FS_FLAG_ALL
    fs_path_flags = cache.fs_path_flags

    visible: set[str] = set()
    match_count = 0

    for path in sorted_paths:
        if path in fp:
            continue  # folder node – visibility decided by its descendants

        # ── Text filter ──────────────────────────────────────────────────
        if pattern is not None:
            basename = path.rsplit("/", 1)[-1]
            if pattern.search(basename) is None:
                continue

        # ── Type filter ──────────────────────────────────────────────────
        if type_filter_active:
            if not (fs_path_flags.get(path, 0) & type_mask):
                continue
            # Suppress meta.ini files inside mods/<subfolder>/
            # (noise entries that only clutter the type-filtered view)
            parts_check = path.split("/")
            if (
                len(parts_check) >= 3
                and parts_check[0].lower() == "mods"
                and parts_check[-1].lower() == "meta.ini"
            ):
                continue

        match_count += 1
        visible.add(path)

        # Mark all ancestor folders visible too.
        parts = path.split("/")
        for i in range(1, len(parts)):
            visible.add("/".join(parts[:i]))

    return visible, match_count
