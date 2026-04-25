"""Phase B: hash verification of size-matched candidates.

Hashes only files that are already size-matched (guaranteed by Phase A).
Uses a per-run in-memory cache keyed by (path, size, mtime) to avoid
recomputing the same file hash twice.

No GUI dependency.
"""

from __future__ import annotations

import os
import time

from ..WabbaHash import WabbaHashXX64, WabbaHashXX64_stream
from .downloads_analysis_types import (
    ArchiveCandidate,
    ArchiveHashResult,
    ArchiveMatchResult,
    CancelCallback,
    DownloadsProgressEvent,
    ProgressCallback,
)
from .downloads_analysis_fileops import ReportLogger

_SMALL_FILE_THRESHOLD = 1024 * 1024   # 1 MiB – hash in-memory below this
_MAX_HASH_MISMATCHES  = 10


def _hash_file(path: str) -> str:
    """Hash *path* and return WabbaHashXX64 base64 string.

    Uses direct in-memory hashing for files < 1 MiB and streaming for larger.
    """
    size = os.path.getsize(path)
    if size < _SMALL_FILE_THRESHOLD:
        with open(path, "rb") as fh:
            data = fh.read()
        return WabbaHashXX64(data)
    else:
        with open(path, "rb") as fh:
            return WabbaHashXX64_stream(fh)


def _cache_key(path: str) -> "tuple | None":
    """Build a cache key (path, size, mtime) for a file, or None on error."""
    try:
        st = os.stat(path)
        return (os.path.abspath(path), st.st_size, st.st_mtime)
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Phase B main entry point
# ---------------------------------------------------------------------------

def run_phase_b(
    match_results: list[ArchiveMatchResult],
    *,
    logger: "ReportLogger | None" = None,
    progress_cb: "ProgressCallback | None" = None,
    cancel_cb: "CancelCallback | None" = None,
    hash_cache: "dict | None" = None,
) -> list[ArchiveHashResult]:
    """Hash-verify candidates and return one ArchiveHashResult per archive.

    Archives are processed in ascending order of Size so smaller files
    are hashed first (faster feedback loop).

    *hash_cache* is an optional shared dict(key→hash_str) that persists
    across multiple calls (same run).  If None a fresh empty dict is used.
    """
    t_start = time.monotonic()
    logger = logger or ReportLogger()
    if hash_cache is None:
        hash_cache = {}

    logger.section("Phase B: hash verification")

    # Sort by size ascending so small files first
    sorted_results = sorted(
        match_results,
        key=lambda r: int(r.archive.get("Size", 0) or 0),
    )

    hash_results: list[ArchiveHashResult] = []
    total = len(sorted_results)
    total_accepted = 0
    total_failed = 0

    for i, mr in enumerate(sorted_results):
        if cancel_cb and cancel_cb():
            break

        archive = mr.archive
        expected_hash = archive.get("Hash", "")
        name = archive.get("Name", "(no name)")
        expected_name = str(name).rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        b_line = f"[B] {i + 1}/{total} {name}"

        hr = ArchiveHashResult(archive=archive)
        hash_mismatches: list[str] = []
        deferred_lines: list[str] = []
        accepted_inline_hash = ""

        if not mr.candidates:
            deferred_lines.append("  no candidates, skip")
            hash_results.append(hr)
            total_failed += 1
            logger.log(b_line)
            for line in deferred_lines:
                logger.log(line)
            if progress_cb:
                progress_cb(DownloadsProgressEvent(
                    phase="Phase B: hashing",
                    current=i + 1,
                    total=total,
                    elapsed=time.monotonic() - t_start,
                    current_archive_name=str(name),
                ))
            continue

        for cand in mr.candidates:
            if cancel_cb and cancel_cb():
                break

            ck = _cache_key(cand.path)
            if ck is not None and ck in hash_cache:
                actual_hash = hash_cache[ck]
                deferred_lines.append(f"  [cached] {cand.filename}")
            else:
                try:
                    actual_hash = _hash_file(cand.path)
                except Exception as exc:
                    hr.error = f"hash error for {cand.path}: {exc}"
                    deferred_lines.append(f"  ERROR hashing {cand.path}: {exc}")
                    continue
                if ck is not None:
                    hash_cache[ck] = actual_hash

            if actual_hash == expected_hash:
                hr.accepted_candidate = cand
                if cand.filename == expected_name:
                    accepted_inline_hash = actual_hash
                else:
                    deferred_lines.append(
                        f"  [ACCEPTED] {cand.filename}  hash={actual_hash}"
                    )
                break
            else:
                if len(hash_mismatches) < _MAX_HASH_MISMATCHES:
                    hash_mismatches.append(
                        f"    hash-mismatch: {cand.filename}  "
                        f"expected={expected_hash}  actual={actual_hash}"
                    )
                    deferred_lines.append(hash_mismatches[-1])

        hr.hash_mismatches = hash_mismatches
        if hr.accepted_candidate is None and not hr.error:
            total_failed += 1
            deferred_lines.append(f"  [no hash match] {name}")
        elif hr.accepted_candidate is not None:
            total_accepted += 1

        if accepted_inline_hash:
            logger.log(f"{b_line} [ACCEPTED] hash={accepted_inline_hash}")
        else:
            logger.log(b_line)
        for line in deferred_lines:
            logger.log(line)

        hash_results.append(hr)

        if progress_cb:
            progress_cb(DownloadsProgressEvent(
                phase="Phase B: hashing",
                current=i + 1,
                total=total,
                elapsed=time.monotonic() - t_start,
                current_archive_name=str(name),
            ))

    elapsed = time.monotonic() - t_start
    logger.log("")
    logger.log(
        f"Phase B done: {total_accepted} accepted, "
        f"{total_failed} failed  ({elapsed:.2f}s)"
    )
    print(
        f"[downloads_analysis] Phase B done: "
        f"{total_accepted} accepted, {total_failed} failed  ({elapsed:.2f}s)"
    )
    return hash_results
