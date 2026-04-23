"""Shared in-memory cache for all data derived from an open WabbaFile.

A ``WabbaCache`` is created and attached to ``WabbaFile.cache`` the moment
a file is opened.  Background threads populate it in three phases:

1. **JSON parse** – ``modlist_data``, ``archives``, ``directives``
2. **Common prep** – ``archives_by_hash`` dict, ``wabba_root_names`` set;
   signals ``prep_done`` once complete.
3. **Per-tab prep** (parallel, each waits on ``prep_done``):
   - Archives tab  → ``archives_ready``
   - Directives tab → ``directive_type_counts``, ``directives_ready``
   - Files tab      → ``fs_directives``, ``fs_sorted_paths``,
                       ``fs_folder_paths``, ``fs_path_flags``, ``files_ready``
   - Problems tab   → ``analysis_result`` / ``analysis_done``
     (progress callbacks stream intermediate updates to the GUI)

GUI panels read from the cache; ``threading.Event`` objects synchronise
producers and consumers.  Set ``cancelled = True`` to abort all workers.

A separate :class:`DiffCache` is created for each compare session and holds
pre-computed diff results for the ``D:Archives`` and ``D:Directives`` tabs.
"""

import threading
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .analysis import AnalysisResult
    from .virtual_list_model import VirtualListModel


# ---------------------------------------------------------------------------
# Directive-type bitmask constants used by fs_path_flags (Files tab).
# Each file path accumulates the OR of flags for every directive that
# targets it.  The same constants are used by _FsTreePanel checkboxes.
# ---------------------------------------------------------------------------

FS_FLAG_INLINE = 1          # InlineFile
FS_FLAG_FROM_ARCHIVE = 2    # FromArchive
FS_FLAG_PATCHED = 4         # PatchedFromArchive
FS_FLAG_OTHER = 8           # RemappedInlineFile and all other types
FS_FLAG_ALL = 15            # all four flags ORed together


class WabbaCache:
    """Holds all pre-computed data derived from a single open WabbaFile."""

    def __init__(self) -> None:
        # ── Phase 1: JSON parse ─────────────────────────────────────────
        self.modlist_data: dict | None = None
        self.archives: list = []
        self.directives: list = []

        # ── Phase 2: common prep ────────────────────────────────────────
        # hash string → full archive entry dict
        self.archives_by_hash: dict[str, dict] = {}
        # filenames that live at the root of the wabba zip
        self.wabba_root_names: set[str] = set()
        # UUID filename → (CRC32, uncompressed_size) for every root entry.
        # SourceDataID and PatchID in directives are UUID filenames stored at
        # the archive root; comparing them by content signature lets the diff
        # logic treat differently-named but identical inline files as equal.
        self.wabba_root_info: dict[str, tuple[int, int]] = {}

        # ── Phase 3: per-tab pre-computed data ──────────────────────────
        # Directives tab: $type → count
        self.directive_type_counts: Counter | None = None
        # Directives tab: pre-computed label strings + virtual list model
        self.directive_labels: list[str] = []
        self.directive_model: "VirtualListModel | None" = None
        # Archives tab: pre-computed label strings + virtual list model
        self.archive_labels: list[str] = []
        self.archive_model: "VirtualListModel | None" = None
        # Files tab: normalised (path, directive_dict) pairs
        self.fs_directives: list[tuple[str, dict]] | None = None
        # Files tab: pre-sorted tree insertion paths
        self.fs_sorted_paths: list[str] | None = None
        # Files tab: which paths represent folders (have children)
        self.fs_folder_paths: set[str] | None = None
        # Files tab: parent_path → sorted list of direct child paths
        self.fs_children: dict[str, list[str]] | None = None
        # Files tab: file-path → OR of FS_FLAG_* for every directive targeting it
        self.fs_path_flags: dict[str, int] = {}
        # Archives tab: hash → list of directives that reference this archive
        # (via ArchiveHashPath[0] or Hash on FromArchive / PatchedFromArchive).
        # Built during run_archives_prep; read-only once archives_ready is set.
        self.archive_to_directives: dict[str, list[dict]] = {}
        # Problems tab: last partial result for live progress display
        self.analysis_progress: "AnalysisResult | None" = None
        # Problems tab: completed analysis result (None while in-progress)
        self.analysis_result: "AnalysisResult | None" = None
        # Problems tab: True once analysis_result is final
        self.analysis_done: bool = False

        # ── Synchronisation events ──────────────────────────────────────
        self.prep_done: threading.Event = threading.Event()
        self.archives_ready: threading.Event = threading.Event()
        self.directives_ready: threading.Event = threading.Event()
        self.files_ready: threading.Event = threading.Event()

        # ── Cancellation ────────────────────────────────────────────────
        # Set to True to abort all workers associated with this cache.
        self.cancelled: bool = False


class DiffCache:
    """Holds pre-computed diff results for a single compare session.

    Created once when compare mode starts and cancelled/discarded when the
    session ends.  Background threads write to it exactly once (after
    signalling the corresponding ``*_ready`` event the data becomes
    read-only), so no per-access locking is needed.

    Population sequence::

        run_diff_archives_prep(cache_a, cache_b, diff_cache)
            waits on cache_a.archives_ready + cache_b.archives_ready
            → fills diff_archives_items, signals diff_archives_ready

        run_diff_directives_prep(cache_a, cache_b, diff_cache)
            waits on cache_a.directives_ready + cache_b.directives_ready
            → fills diff_directives_items, signals diff_directives_ready
    """

    def __init__(self) -> None:
        # D:Archives tab pre-computed diff items
        self.diff_archives_items: list[dict] = []
        self.diff_archives_ready: threading.Event = threading.Event()
        # D:Directives tab pre-computed diff items
        self.diff_directives_items: list[dict] = []
        self.diff_directives_ready: threading.Event = threading.Event()
        # Set to True to abort all workers associated with this diff cache.
        self.cancelled: bool = False
