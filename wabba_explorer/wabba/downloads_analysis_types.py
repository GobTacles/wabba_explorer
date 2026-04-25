"""Data models for the downloads-analysis multi-phase workflow.

No GUI dependency; safe to import from any thread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Operation-mode constants
# ---------------------------------------------------------------------------

MODE_MOVE_COPY = "move_copy"
MODE_VERIFY    = "verify"
MODE_FIND_ONE  = "find_one"

# Archive file action (move_copy mode)
ARCHIVE_ACTION_MOVE = "move"
ARCHIVE_ACTION_COPY = "copy"

# .meta handling action
META_ACTION_MOVE     = "move"
META_ACTION_COPY     = "copy"
META_ACTION_EXPORT   = "export"
META_ACTION_GENERATE = "generate"
META_ACTION_SKIP     = "skip"

# Missing-.meta fallback
META_FALLBACK_EXPORT   = "export"
META_FALLBACK_GENERATE = "generate"
META_FALLBACK_SKIP     = "skip"


# ---------------------------------------------------------------------------
# Operation request
# ---------------------------------------------------------------------------

@dataclass
class DownloadsOperationRequest:
    """All parameters needed to run a downloads operation.

    *mode* is one of the MODE_* constants.
    For MODE_VERIFY, *dest_folder*, *archive_action*, *meta_action* and
    *meta_fallback* are ignored.
    For MODE_FIND_ONE, only *shared_folder* and *target_archive* are used.
    """
    mode: str
    shared_folder: str

    # Move/copy mode only
    dest_folder: str = ""
    archive_action: str = ARCHIVE_ACTION_COPY   # "move" or "copy"
    meta_action: str = META_ACTION_SKIP         # see META_ACTION_* constants
    meta_fallback: str = META_FALLBACK_SKIP     # only relevant when meta_action is move/copy

    # find_one mode only: the single archive dict from the modlist
    target_archive: "dict | None" = None


# ---------------------------------------------------------------------------
# Per-archive candidate
# ---------------------------------------------------------------------------

@dataclass
class ArchiveCandidate:
    """A single file in the shared folder that may match an archive entry."""
    path: str           # absolute path on disk
    filename: str       # basename
    file_size: int      # bytes


# ---------------------------------------------------------------------------
# Per-archive match result (after Phase A)
# ---------------------------------------------------------------------------

@dataclass
class ArchiveMatchResult:
    """Candidates and size-mismatch diagnostics for one archive entry."""
    archive: dict                            # the archive entry dict
    candidates: list[ArchiveCandidate] = field(default_factory=list)
    # First ≤10 files whose name matched but size differed
    size_mismatches: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-archive hash result (after Phase B)
# ---------------------------------------------------------------------------

@dataclass
class ArchiveHashResult:
    """Outcome of hash verification for one archive entry."""
    archive: dict
    accepted_candidate: "ArchiveCandidate | None" = None  # None = no hash match
    # First ≤10 hash mismatches: "filename: expected=… actual=…"
    hash_mismatches: list[str] = field(default_factory=list)
    error: str = ""   # non-empty if hashing itself failed


# ---------------------------------------------------------------------------
# Per-archive materialization result (after Phase C)
# ---------------------------------------------------------------------------

@dataclass
class ArchiveMaterializeResult:
    """Outcome of move/copy for one archive entry."""
    archive: dict
    dest_archive_path: str = ""  # empty if not moved/copied
    dest_meta_path: str = ""     # empty if meta not handled
    skipped: bool = False        # no accepted candidate
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Progress event (emitted by engine to GUI)
# ---------------------------------------------------------------------------

@dataclass
class DownloadsProgressEvent:
    """Snapshot of progress for GUI display.  Emitted periodically."""
    phase: str          # e.g. "Phase A: scanning", "Phase B: hashing", "Phase C: moving"
    current: int        # archives processed in this phase
    total: int          # total archives in this phase
    elapsed: float      # seconds since operation start
    current_archive_name: str = ""


# ---------------------------------------------------------------------------
# Aggregated final report
# ---------------------------------------------------------------------------

@dataclass
class DownloadsOperationReport:
    """Final result returned after the operation finishes (or is cancelled)."""
    mode: str
    cancelled: bool = False
    aborted_early: str = ""   # non-empty = reason for early abort (pre-phase A)

    # Phase A results
    match_results: list[ArchiveMatchResult] = field(default_factory=list)
    archives_with_candidates: int = 0
    archives_without_candidates: int = 0
    phase_a_elapsed: float = 0.0

    # Phase B results
    hash_results: list[ArchiveHashResult] = field(default_factory=list)
    archives_accepted: int = 0
    archives_hash_failed: int = 0
    phase_b_elapsed: float = 0.0

    # Phase C results (move/copy mode only)
    materialize_results: list[ArchiveMaterializeResult] = field(default_factory=list)
    archives_moved_copied: int = 0
    phase_c_elapsed: float = 0.0

    # All log lines (phase headers + errors + summaries)
    log_lines: list[str] = field(default_factory=list)

    # Path of the saved log file (empty if not saved)
    saved_log_path: str = ""


# ---------------------------------------------------------------------------
# Callback type aliases (documentation only; Python doesn't enforce them)
# ---------------------------------------------------------------------------

# progress_callback(event: DownloadsProgressEvent) -> None
ProgressCallback = Callable[["DownloadsProgressEvent"], None]

# should_cancel() -> bool
CancelCallback = Callable[[], bool]

# log_callback(line: str) -> None  (used to mirror major steps to console)
LogCallback = Callable[[str], None]

# confirm_callback(title: str, summary: str, details: str) -> bool
# Blocks the calling thread until user responds.  Returns True to continue.
ConfirmCallback = Callable[[str, str, str], bool]
