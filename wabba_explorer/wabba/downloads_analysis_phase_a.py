"""Phase A: early scan + filename/size candidate search.

Builds a cache of files in the shared downloads folder, then for each
archive entry finds candidate files by exact filename match and
prefix-shortening matches.  No hashes are computed here.

No GUI dependency.
"""

from __future__ import annotations

import os
import time

from .downloads_analysis_types import (
    ArchiveCandidate,
    ArchiveMatchResult,
    CancelCallback,
    DownloadsProgressEvent,
    ProgressCallback,
)
from .downloads_analysis_fileops import (
    ReportLogger,
    check_free_space,
    same_drive,
)

_MAX_CANDIDATES       = 10
_MAX_SIZE_MISMATCHES  = 10


# ---------------------------------------------------------------------------
# Shared-folder index
# ---------------------------------------------------------------------------

def build_folder_index(folder: str) -> dict[str, list[tuple[int, str]]]:
    """Scan *folder* and return a filename→[(size, path), …] index.

    Files ending in .meta are excluded.
    Only immediate files (not recursive) are indexed.
    """
    index: dict[str, list[tuple[int, str]]] = {}
    try:
        entries = os.scandir(folder)
    except OSError:
        return index
    with entries:
        for entry in entries:
            if not entry.is_file(follow_symlinks=False):
                continue
            if entry.name.lower().endswith(".meta"):
                continue
            try:
                size = entry.stat().st_size
            except OSError:
                continue
            index.setdefault(entry.name, []).append((size, entry.path))
    return index


# ---------------------------------------------------------------------------
# Candidate search for one archive
# ---------------------------------------------------------------------------

def find_candidates(
    archive: dict,
    folder_index: dict[str, list[tuple[int, str]]],
    *,
    logger: "ReportLogger | None" = None,
) -> ArchiveMatchResult:
    """Search *folder_index* for candidate files matching *archive*.

    Candidate ordering:
    1. Exact filename + exact size match.
    2. For each prefix-shortening step (trim one char from right of the
       archive's filename stem), check each file whose filename starts with
       the current prefix for a size match.

    Stops once 10 candidates are collected.
    Records up to 10 size mismatches for logging.
    """
    archive_name: str = archive.get("Name", "") or ""
    # Normalise to bare basename
    archive_name = archive_name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    expected_size: int = int(archive.get("Size", 0) or 0)

    result = ArchiveMatchResult(archive=archive)
    seen_paths: set[str] = set()
    size_mismatches: list[str] = []

    def _try_add(filename: str, entries: list[tuple[int, str]]) -> bool:
        """Try each entry under *filename*.  Returns True if candidates are full."""
        for fsize, fpath in entries:
            if fpath in seen_paths:
                continue
            if fsize == expected_size:
                seen_paths.add(fpath)
                result.candidates.append(
                    ArchiveCandidate(path=fpath, filename=filename, file_size=fsize)
                )
                if logger:
                    logger.log(f"  [size-match] {fpath}")
                if len(result.candidates) >= _MAX_CANDIDATES:
                    return True
            else:
                if len(size_mismatches) < _MAX_SIZE_MISMATCHES:
                    size_mismatches.append(
                        f"    size-mismatch: {filename}  "
                        f"expected={expected_size}  actual={fsize}"
                    )
        return False

    # --- Exact filename match ---
    if archive_name in folder_index:
        if _try_add(archive_name, folder_index[archive_name]):
            result.size_mismatches = size_mismatches
            return result

    # --- Prefix-shortening matches ---
    # Build a sorted list of index keys once so we iterate deterministically.
    all_filenames = list(folder_index.keys())

    prefix = archive_name
    while len(prefix) > 1:
        prefix = prefix[:-1]   # shorten by one character
        lower_prefix = prefix.lower()
        for fname in all_filenames:
            if not fname.lower().startswith(lower_prefix):
                continue
            # Skip exact filename (already handled above or it didn't match size)
            if fname == archive_name:
                continue
            if _try_add(fname, folder_index[fname]):
                result.size_mismatches = size_mismatches
                return result

    result.size_mismatches = size_mismatches
    return result


# ---------------------------------------------------------------------------
# Phase A main entry point
# ---------------------------------------------------------------------------

def run_phase_a(
    archives: list[dict],
    shared_folder: str,
    *,
    dest_folder: str = "",
    check_dest_space: bool = False,
    archive_action: str = "copy",
    logger: "ReportLogger | None" = None,
    progress_cb: "ProgressCallback | None" = None,
    cancel_cb: "CancelCallback | None" = None,
) -> "tuple[list[ArchiveMatchResult], str]":
    """Run Phase A for *archives* scanned from *shared_folder*.

    Returns (match_results, abort_reason).
    *abort_reason* is non-empty when the phase cannot continue (e.g.
    insufficient destination space).

    Parameters
    ----------
    archives:
        List of archive entry dicts from the modlist.
    shared_folder:
        Path to the shared downloads folder.
    dest_folder:
        Required when *check_dest_space* is True.
    check_dest_space:
        When True, sum archive sizes and abort if dest_folder lacks space
        (skipped when archive_action is "move" and shared/dest are same drive).
    archive_action:
        "move" or "copy".
    logger, progress_cb, cancel_cb:
        Optional callbacks.
    """
    t_start = time.monotonic()
    logger = logger or ReportLogger()

    logger.section("Phase A: scan + candidate search")
    logger.log(f"Shared folder: {shared_folder}")

    # ── Build folder index ──────────────────────────────────────────
    logger.log("Scanning shared downloads folder…")
    folder_index = build_folder_index(shared_folder)
    logger.log(
        f"  {sum(len(v) for v in folder_index.values())} files indexed "
        f"({len(folder_index)} unique filenames)"
    )

    if cancel_cb and cancel_cb():
        return [], "cancelled"

    # ── Early free-space check ──────────────────────────────────────
    if check_dest_space and dest_folder:
        skip_space_check = (
            archive_action == "move"
            and same_drive(shared_folder, dest_folder)
        )
        if not skip_space_check:
            total_size = sum(
                int(a.get("Size", 0) or 0)
                for a in archives
                if isinstance(a, dict)
            )
            error = check_free_space(dest_folder, total_size)
            if error:
                logger.log(f"ERROR (free-space pre-check): {error}")
                return [], error

    # ── Candidate search for each archive ──────────────────────────
    match_results: list[ArchiveMatchResult] = []
    total = len(archives)

    for i, archive in enumerate(archives):
        if cancel_cb and cancel_cb():
            return match_results, "cancelled"

        if not isinstance(archive, dict):
            continue

        name = archive.get("Name", "(no name)")
        logger.log(f"[A] {i + 1}/{total} {name}")

        mr = find_candidates(archive, folder_index, logger=logger)

        if mr.size_mismatches:
            for line in mr.size_mismatches:
                logger.log(line)

        match_results.append(mr)

        if progress_cb:
            progress_cb(DownloadsProgressEvent(
                phase="Phase A: scanning",
                current=i + 1,
                total=total,
                elapsed=time.monotonic() - t_start,
                current_archive_name=str(name),
            ))

    # ── Summary ────────────────────────────────────────────────────
    with_candidates = sum(1 for r in match_results if r.candidates)
    without_candidates = len(match_results) - with_candidates
    elapsed = time.monotonic() - t_start
    logger.log("")
    logger.log(
        f"Phase A done: {with_candidates} archives have candidates, "
        f"{without_candidates} have none  ({elapsed:.2f}s)"
    )
    print(
        f"[downloads_analysis] Phase A done: "
        f"{with_candidates} with candidates, "
        f"{without_candidates} without  ({elapsed:.2f}s)"
    )
    return match_results, ""
