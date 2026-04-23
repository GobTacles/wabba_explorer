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

For compare mode, after both side caches have been loaded::

    run_diff_archives_prep(cache_a, cache_b, diff_cache)
    run_diff_directives_prep(cache_a, cache_b, diff_cache)

The high-level :func:`run_pipeline` function orchestrates the single-file
loading sequence and accepts callbacks so it can be driven from any context
(GUI or tests) without importing tkinter.
"""

import json
import threading
import time
from collections import Counter
from typing import Callable

from .cache import WabbaCache, DiffCache
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

def run_prep(wabba, cache: WabbaCache, label: str = "") -> None:
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
        root_names = wabba.list_root_files()
        cache.wabba_root_names = set(root_names)
    except Exception:
        root_names = []
        cache.wabba_root_names = set()

    # Build wabba_root_info: UUID filename → (CRC32, uncompressed_size).
    # Using ZipInfo metadata only – no actual decompression required.
    root_info: dict[str, tuple[int, int]] = {}
    for name in root_names:
        try:
            zi = wabba.get_zip_info(name)
            root_info[name] = (zi.CRC, zi.file_size)
        except Exception:
            pass
    cache.wabba_root_info = root_info

    cache.prep_done.set()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    _label = f"[{label}] " if label else ""
    print(
        f"[bg] {_label}common prep done  "
        f"({len(cache.archives)} archives, {len(cache.wabba_root_names)} root files, "
        f"{len(cache.wabba_root_info)} root entries with size/CRC, "
        f"{elapsed_ms} ms)"
    )


# ---------------------------------------------------------------------------
# Phase 3: per-tab prep (each waits on prep_done)
# ---------------------------------------------------------------------------

def run_archives_prep(cache: WabbaCache, label: str = "") -> None:
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

    # Build archive_to_directives index: hash → [directive, …]
    # Single pass over all directives; covers FromArchive and PatchedFromArchive.
    atd: dict[str, list[dict]] = {}
    for d in cache.directives:
        if not isinstance(d, dict):
            continue
        dtype = d.get("$type", "")
        if dtype not in ("FromArchive", "PatchedFromArchive"):
            continue
        ahp = d.get("ArchiveHashPath")
        h = (ahp[0] if ahp else None) or d.get("Hash", "")
        if h:
            atd.setdefault(h, []).append(d)
    cache.archive_to_directives = atd

    cache.archives_ready.set()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    _label = f"[{label}] " if label else ""
    print(f"[bg] {_label}archives prep done  ({len(cache.archives)} entries, {elapsed_ms} ms)")


def run_directives_prep(cache: WabbaCache, label: str = "") -> None:
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
    _label = f"[{label}] " if label else ""
    print(f"[bg] {_label}directives prep done  ({len(cache.directives)} entries, {elapsed_ms} ms)")


def run_files_prep(cache: WabbaCache, label: str = "") -> None:
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

    time.sleep(0)  # yield GIL to the main thread before heavy work

    # Build folder_paths and all_paths in a single pass.
    folder_paths: set[str] = set()
    all_paths: set[str] = set()
    for norm, _ in fs_dirs:
        parts = norm.split("/")
        for i in range(len(parts)):
            prefix = "/".join(parts[: i + 1])
            all_paths.add(prefix)
            if i < len(parts) - 1:
                folder_paths.add(prefix)

    if cache.cancelled:
        return

    time.sleep(0)  # yield GIL before sort-key pre-computation

    # Pre-compute sort keys once so the sort uses dict.__getitem__ (a C call)
    # instead of a Python function that allocates a new list on every comparison.
    # This avoids O(n log n) list allocations and dramatically reduces GIL hold time.
    sort_keys: dict[str, tuple] = {}
    for path in all_paths:
        parts = path.split("/")
        sort_keys[path] = tuple(
            (0 if "/".join(parts[: i + 1]) in folder_paths else 1, parts[i].lower())
            for i in range(len(parts))
        )

    if cache.cancelled:
        return

    time.sleep(0)  # yield GIL before sort

    cache.fs_folder_paths = folder_paths
    sorted_paths = sorted(all_paths, key=sort_keys.__getitem__)
    cache.fs_sorted_paths = sorted_paths

    time.sleep(0)  # yield GIL after sort, before children-map build

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
    _label = f"[{label}] " if label else ""
    print(f"[bg] {_label}files prep done  ({len(cache.fs_sorted_paths)} tree nodes, {elapsed_ms} ms)")


# ---------------------------------------------------------------------------
# High-level pipeline orchestrator (GUI-agnostic)
# ---------------------------------------------------------------------------

def run_pipeline(
    wabba,
    cache: WabbaCache,
    label: str = "",
    *,
    on_phase1_done,
    on_pipeline_started,
    extra_workers: "list[Callable[[], None]] | None" = None,
) -> None:
    """Run the full single-file loading pipeline in the calling thread.

    Intended to be run inside a ``threading.Thread`` started by the GUI.
    Uses only callbacks to communicate results back – no tkinter dependency.

    Sequence:

    1. ``parse_modlist`` → ``on_phase1_done(wabba, cache)``
    2. ``run_prep``
    3. Launch per-tab prep threads (archives, directives, files)
       + any *extra_workers* (e.g. the problems-analysis thread).
    4. ``on_pipeline_started(cache)``

    Parameters
    ----------
    wabba:
        Open ``WabbaFile`` instance.
    cache:
        ``WabbaCache`` attached to *wabba*.
    label:
        Short side label for log messages (``"A"``, ``"B"``, or ``""``).
    on_phase1_done:
        ``callable(wabba, cache)`` – called immediately after
        ``parse_modlist`` so the main/modlist tab can be populated
        without waiting for the heavier prep steps.
    on_pipeline_started:
        ``callable(cache)`` – called after all per-tab threads have
        been launched, signalling the UI to begin tab-ready polling.
    extra_workers:
        Optional list of zero-argument callables to launch as daemon
        threads alongside the three standard per-tab prep threads.
    """
    parse_modlist(wabba, cache)
    on_phase1_done(wabba, cache)

    if cache.cancelled:
        return
    run_prep(wabba, cache, label=label)

    if cache.cancelled:
        return

    _side = f"[{label}] " if label else ""
    print(f"[wabba_explorer] {_side}Archives: {len(cache.archives)} entries")
    print(f"[wabba_explorer] {_side}Directives: {len(cache.directives)} entries")

    threading.Thread(
        target=run_archives_prep, args=(cache,), kwargs={"label": label}, daemon=True
    ).start()
    threading.Thread(
        target=run_directives_prep, args=(cache,), kwargs={"label": label}, daemon=True
    ).start()
    threading.Thread(
        target=run_files_prep, args=(cache,), kwargs={"label": label}, daemon=True
    ).start()

    for worker in (extra_workers or []):
        threading.Thread(target=worker, daemon=True).start()

    on_pipeline_started(cache)


# ---------------------------------------------------------------------------
# Compare-mode diff prep
# ---------------------------------------------------------------------------

def run_diff_archives_prep(
    cache_a: WabbaCache,
    cache_b: WabbaCache,
    diff_cache: DiffCache,
    label: str = "",
) -> None:
    """Pre-compute the archive diff and signal ``diff_cache.diff_archives_ready``.

    Waits for both ``cache_a.archives_ready`` and ``cache_b.archives_ready``
    before calling :func:`wabba.diff.diff_archives`.  The result is stored in
    ``diff_cache.diff_archives_items`` and then the event is set so the GUI
    can populate the ``D:Archives`` tab without blocking.
    """
    cache_a.archives_ready.wait()
    if diff_cache.cancelled:
        return
    cache_b.archives_ready.wait()
    if diff_cache.cancelled:
        return

    from .diff import diff_archives
    t0 = time.monotonic()
    diff_cache.diff_archives_items = diff_archives(cache_a, cache_b)
    diff_cache.diff_archives_ready.set()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    items = diff_cache.diff_archives_items
    updated = sum(1 for i in items if i.get("_diff_side") == "updated")
    removed = sum(1 for i in items if i.get("_diff_side") == "removed")
    added = sum(1 for i in items if i.get("_diff_side") == "added")
    _label = f"[{label}] " if label else ""
    print(
        f"[bg] {_label}diff archives prep done  "
        f"({updated} updated, {removed} removed, {added} added, {elapsed_ms} ms)"
    )


def run_diff_directives_prep(
    cache_a: WabbaCache,
    cache_b: WabbaCache,
    diff_cache: DiffCache,
    label: str = "",
) -> None:
    """Pre-compute the directive diff and signal ``diff_cache.diff_directives_ready``.

    Waits for both ``cache_a.directives_ready`` and ``cache_b.directives_ready``
    (which also guarantees ``wabba_root_info`` is populated since it is built
    during ``run_prep``, which ``run_directives_prep`` waits on).
    """
    cache_a.directives_ready.wait()
    if diff_cache.cancelled:
        return
    cache_b.directives_ready.wait()
    if diff_cache.cancelled:
        return

    from .diff import diff_directives
    t0 = time.monotonic()
    diff_cache.diff_directives_items = diff_directives(cache_a, cache_b)
    diff_cache.diff_directives_ready.set()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    items = diff_cache.diff_directives_items
    a_only = sum(1 for i in items if i.get("_diff_side") == "A only")
    b_only = sum(1 for i in items if i.get("_diff_side") == "B only")
    changed = sum(1 for i in items if i.get("_diff_side") == "changed") // 2
    _label = f"[{label}] " if label else ""
    print(
        f"[bg] {_label}diff directives prep done  "
        f"({a_only} A-only, {b_only} B-only, {changed} changed pairs, "
        f"{len(items)} total rows, {elapsed_ms} ms)"
    )
