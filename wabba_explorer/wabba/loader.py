"""Pure background loading functions for WabbaFile data.

All functions here are free of GUI dependencies and safe to call from any
thread.  They populate a :class:`WabbaCache` step by step.

Typical call sequence (each step can be in a separate thread)::

    parse_modlist(wabba, cache)          # reads + parses modlist JSON
    run_prep(wabba, cache)               # builds shared lookup caches
    # Then any/all of the following in parallel:
    run_archives_prep(cache)             # builds label strings + model
    run_directives_prep(cache)           # counts $types, builds label strings + model
    run_files_prep(cache)                # builds sorted fs-tree data + children map
"""

import json
import time
from collections import Counter

from .cache import WabbaCache
from .cache import FS_FLAG_INLINE, FS_FLAG_FROM_ARCHIVE, FS_FLAG_PATCHED, FS_FLAG_OTHER
from .label_util import archive_label, directive_label
from .virtual_list_model import VirtualListModel


# ---------------------------------------------------------------------------
# Phase 1: JSON parse
# ---------------------------------------------------------------------------

def parse_modlist(wabba, cache: WabbaCache) -> None:
    """Read and parse the ``modlist`` JSON entry; populate cache phase-1 fields.

    On success ``cache.modlist_data``, ``cache.archives`` and
    ``cache.directives`` are populated.  Errors are printed to stdout
    so they appear in the GUI console.
    """
    try:
        raw = wabba.read_modlist()
    except FileNotFoundError:
        print("[wabba_explorer] 'modlist' entry not found in archive")
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"[wabba_explorer] modlist JSON parse error: {exc}")
        return

    if not isinstance(data, dict):
        print(
            f"[wabba_explorer] modlist root is not a JSON object "
            f"(type={type(data).__name__})"
        )
        return

    cache.modlist_data = data
    raw_archives = data.get("Archives", [])
    cache.archives = raw_archives if isinstance(raw_archives, list) else []
    raw_directives = data.get("Directives", [])
    cache.directives = raw_directives if isinstance(raw_directives, list) else []


# ---------------------------------------------------------------------------
# Phase 2: common prep (must run after parse_modlist)
# ---------------------------------------------------------------------------

def run_prep(wabba, cache: WabbaCache) -> None:
    """Build shared lookup caches and signal ``cache.prep_done``.

    Builds:
    - ``cache.archives_by_hash`` – hash string → full archive entry dict
    - ``cache.wabba_root_names`` – set of root-level filenames in the wabba zip

    Must be called after :func:`parse_modlist`.
    Sets ``cache.prep_done`` when complete so tab workers can proceed.
    """
    if cache.cancelled:
        return

    t0 = time.monotonic()
    cache.archives_by_hash = {
        a["Hash"]: a
        for a in cache.archives
        if isinstance(a, dict) and "Hash" in a
    }

    try:
        cache.wabba_root_names = set(wabba.list_root_files())
    except Exception:
        cache.wabba_root_names = set()

    cache.prep_done.set()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    print(
        f"[bg] common prep done  "
        f"({len(cache.archives)} archives, {len(cache.wabba_root_names)} root files, "
        f"{elapsed_ms} ms)"
    )


# ---------------------------------------------------------------------------
# Phase 3: per-tab prep (each waits on prep_done)
# ---------------------------------------------------------------------------

def run_archives_prep(cache: WabbaCache) -> None:
    """Pre-compute archive label strings + virtual list model; signal ``archives_ready``.

    The archives list is already populated by :func:`parse_modlist`; this
    function only waits for the common prep to finish (so that
    ``archives_by_hash`` is available for cross-reference) before building
    the model and setting the ``archives_ready`` event.
    """
    cache.prep_done.wait()
    if cache.cancelled:
        return
    t0 = time.monotonic()
    labels = [archive_label(a) for a in cache.archives]
    cache.archive_labels = labels
    model = VirtualListModel()
    model.set_data(cache.archives, labels)
    cache.archive_model = model
    cache.archives_ready.set()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    print(f"[bg] archives prep done  ({len(cache.archives)} entries, {elapsed_ms} ms)")


def run_directives_prep(cache: WabbaCache) -> None:
    """Pre-compute ``$type`` counts, label strings, and virtual list model; signal ``directives_ready``."""
    cache.prep_done.wait()
    if cache.cancelled:
        return
    t0 = time.monotonic()
    counts: Counter = Counter()
    labels: list[str] = []
    for d in cache.directives:
        if isinstance(d, dict):
            counts[d.get("$type", "(none)")] += 1
        labels.append(directive_label(d) if isinstance(d, dict) else str(d))
    cache.directive_type_counts = counts
    cache.directive_labels = labels
    model = VirtualListModel()
    model.set_data(cache.directives, labels)
    cache.directive_model = model
    cache.directives_ready.set()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    print(f"[bg] directives prep done  ({len(cache.directives)} entries, {elapsed_ms} ms)")


def run_files_prep(cache: WabbaCache) -> None:
    """Pre-compute the normalised directive list and sorted tree order.

    Populates:
    - ``cache.fs_directives``   – list of ``(norm_path, directive_dict)``
    - ``cache.fs_sorted_paths`` – pre-sorted list of all tree node paths
    - ``cache.fs_folder_paths`` – set of paths that are parent folders

    Signals ``cache.files_ready`` on completion.
    """
    cache.prep_done.wait()
    if cache.cancelled:
        return

    t0 = time.monotonic()
    fs_dirs: list[tuple[str, dict]] = []
    for d in cache.directives:
        if not isinstance(d, dict):
            continue
        to = d.get("To", "")
        if not to:
            continue
        norm = to.replace("\\", "/").rstrip("/")
        if norm:
            fs_dirs.append((norm, d))
    cache.fs_directives = fs_dirs

    if cache.cancelled:
        return

    # Build per-file directive-type bitmask (leaf paths only).
    _dtype_to_flag = {
        "InlineFile": FS_FLAG_INLINE,
        "FromArchive": FS_FLAG_FROM_ARCHIVE,
        "PatchedFromArchive": FS_FLAG_PATCHED,
    }
    fs_path_flags: dict[str, int] = {}
    for norm, d in fs_dirs:
        flag = _dtype_to_flag.get(d.get("$type", ""), FS_FLAG_OTHER)
        fs_path_flags[norm] = fs_path_flags.get(norm, 0) | flag
    cache.fs_path_flags = fs_path_flags

    if cache.cancelled:
        return

    # Identify folder paths (paths that are a prefix of at least one other path)
    folder_paths: set[str] = set()
    for norm, _ in fs_dirs:
        parts = norm.split("/")
        for i in range(1, len(parts)):
            folder_paths.add("/".join(parts[:i]))

    # All paths that need a tree node (including intermediate folders)
    all_paths: set[str] = set()
    for norm, _ in fs_dirs:
        parts = norm.split("/")
        for i in range(1, len(parts) + 1):
            all_paths.add("/".join(parts[:i]))

    def _sort_key(path: str):
        parts = path.split("/")
        return [
            (0 if "/".join(parts[: i + 1]) in folder_paths else 1, parts[i].lower())
            for i in range(len(parts))
        ]

    cache.fs_folder_paths = folder_paths
    sorted_paths = sorted(all_paths, key=_sort_key)
    cache.fs_sorted_paths = sorted_paths

    # Build children map: parent_path → sorted list of direct child paths.
    # Since sorted_paths is already in the desired tree order, iterating it
    # preserves that order in each children list.
    fs_children: dict[str, list[str]] = {}
    for path in sorted_paths:
        parts = path.split("/")
        parent = "/".join(parts[:-1])
        if parent not in fs_children:
            fs_children[parent] = []
        fs_children[parent].append(path)
    cache.fs_children = fs_children

    cache.files_ready.set()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    print(f"[bg] files prep done  ({len(cache.fs_sorted_paths)} tree nodes, {elapsed_ms} ms)")
