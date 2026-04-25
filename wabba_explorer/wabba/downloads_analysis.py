"""High-level orchestrator for the downloads-analysis multi-phase workflow.

This module is the single entry point from the GUI layer.  It calls
Phase A, B, and C in the right sequence for each mode, handles callbacks,
and builds the final DownloadsOperationReport.

No GUI dependency.
"""

from __future__ import annotations

import os
import time
from datetime import datetime

from .downloads_analysis_types import (
    ARCHIVE_ACTION_MOVE,
    MODE_FIND_ONE,
    MODE_MOVE_COPY,
    MODE_VERIFY,
    CancelCallback,
    ConfirmCallback,
    DownloadsOperationReport,
    DownloadsOperationRequest,
    LogCallback,
    ProgressCallback,
)
from .downloads_analysis_fileops import ReportLogger, make_log_filename
from .downloads_analysis_phase_a import run_phase_a
from .downloads_analysis_phase_b import run_phase_b
from .downloads_analysis_phase_c import run_phase_c


def _is_game_file_source_archive(archive: object) -> bool:
    """Return True when *archive* is a GameFileSourceDownloader entry."""
    if not isinstance(archive, dict):
        return False
    state = archive.get("State")
    if not isinstance(state, dict):
        return False
    state_type = str(state.get("$type", "") or "")
    return "GameFileSourceDownloader" in state_type


def _build_pre_hash_details(report: DownloadsOperationReport) -> str:
    """Build a human-readable pre-hash summary string for the confirm dialog."""
    lines = [
        f"Archives with size-match candidates : {report.archives_with_candidates}",
        f"Archives with no candidates          : {report.archives_without_candidates}",
    ]
    if report.archives_without_candidates:
        lines.append("")
        lines.append("Archives without candidates:")
        for mr in report.match_results:
            if not mr.candidates:
                name = mr.archive.get("Name", "(no name)")
                lines.append(f"  - {name}")
    return "\n".join(lines)


def _build_post_hash_details(report: DownloadsOperationReport) -> str:
    """Build a human-readable post-hash summary string for the confirm dialog."""
    lines = [
        f"Hash-verified (accepted) : {report.archives_accepted}",
        f"No hash match / skipped  : {report.archives_hash_failed}",
    ]
    failed = [hr for hr in report.hash_results if hr.accepted_candidate is None]
    if failed:
        lines.append("")
        lines.append("Archives without a hash match:")
        for hr in failed:
            name = hr.archive.get("Name", "(no name)")
            lines.append(f"  - {name}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_downloads_operation(
    request: DownloadsOperationRequest,
    archives: list[dict],
    *,
    progress_cb: "ProgressCallback | None" = None,
    cancel_cb: "CancelCallback | None" = None,
    log_cb: "LogCallback | None" = None,
    pre_hash_confirm_cb: "ConfirmCallback | None" = None,
    post_hash_confirm_cb: "ConfirmCallback | None" = None,
) -> DownloadsOperationReport:
    """Run the full downloads operation for *request* against *archives*.

    This function is designed to run in a background thread.  All GUI
    interaction is performed through the provided callbacks.

    Parameters
    ----------
    request:
        The fully-populated DownloadsOperationRequest.
    archives:
        List of archive entry dicts from the loaded modlist.  For
        MODE_FIND_ONE the list should contain only the single target archive
        (or filtering is done internally from request.target_archive).
    progress_cb:
        Called periodically with a DownloadsProgressEvent.
    cancel_cb:
        Called before each archive; return True to abort.
    log_cb:
        Called with each major log line for console mirroring.
    pre_hash_confirm_cb:
        Called after Phase A with (title, summary, details).  Must block
        until the user confirms or cancels; return True to proceed.
    post_hash_confirm_cb:
        Called after Phase B with (title, summary, details).  Same semantics.
    """
    t_global = time.monotonic()
    mode = request.mode
    report = DownloadsOperationReport(mode=mode)

    logger = ReportLogger(log_callback=log_cb)
    logger.section(f"Wabba Explorer – downloads operation ({mode})")
    logger.log(f"Started: {datetime.now().isoformat(timespec='seconds')}")
    logger.log(f"Shared folder: {request.shared_folder}")
    if mode == MODE_MOVE_COPY:
        logger.log(f"Destination: {request.dest_folder}")
        logger.log(
            f"Archive action: {request.archive_action}  "
            f"Meta action: {request.meta_action}  "
            f"Meta fallback: {request.meta_fallback}"
        )

    # Narrow to single archive for find_one
    if mode == MODE_FIND_ONE:
        if request.target_archive is not None:
            archives = [request.target_archive]
        else:
            archives = [a for a in archives if a is request.target_archive]

    # Ignore game-file-source downloader entries for all shared-download tools.
    total_input_archives = len(archives)
    archives = [a for a in archives if not _is_game_file_source_archive(a)]
    ignored_count = total_input_archives - len(archives)
    if ignored_count:
        logger.log(
            "Ignoring "
            f"{ignored_count} archive(s) with State.$type containing "
            "GameFileSourceDownloader."
        )

    # ── Phase A ─────────────────────────────────────────────────────────
    t_a = time.monotonic()
    match_results, abort_reason = run_phase_a(
        archives,
        request.shared_folder,
        dest_folder=request.dest_folder,
        check_dest_space=(mode == MODE_MOVE_COPY),
        archive_action=request.archive_action,
        logger=logger,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
    report.phase_a_elapsed = time.monotonic() - t_a
    report.match_results = match_results
    report.archives_with_candidates = sum(1 for r in match_results if r.candidates)
    report.archives_without_candidates = len(match_results) - report.archives_with_candidates

    if abort_reason == "cancelled":
        report.cancelled = True
        report.log_lines = logger.lines()
        return report

    if abort_reason:
        report.aborted_early = abort_reason
        report.log_lines = logger.lines()
        return report

    # Pre-hash confirmation gate
    if pre_hash_confirm_cb is not None:
        details = _build_pre_hash_details(report)
        proceed = pre_hash_confirm_cb(
            "Confirm: proceed to hash verification",
            f"{report.archives_with_candidates} archives found, "
            f"{report.archives_without_candidates} missing.",
            details,
        )
        if not proceed:
            logger.log("User aborted before Phase B.")
            report.cancelled = True
            report.log_lines = logger.lines()
            return report

    # ── Phase B ─────────────────────────────────────────────────────────
    t_b = time.monotonic()
    hash_cache: dict = {}
    hash_results = run_phase_b(
        match_results,
        logger=logger,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
        hash_cache=hash_cache,
    )
    report.phase_b_elapsed = time.monotonic() - t_b
    report.hash_results = hash_results
    report.archives_accepted = sum(
        1 for hr in hash_results if hr.accepted_candidate is not None
    )
    report.archives_hash_failed = len(hash_results) - report.archives_accepted

    if cancel_cb and cancel_cb():
        report.cancelled = True
        report.log_lines = logger.lines()
        return report

    # Post-hash confirmation gate (not shown for find_one – no destructive action)
    if mode != MODE_FIND_ONE and post_hash_confirm_cb is not None:
        details = _build_post_hash_details(report)
        proceed = post_hash_confirm_cb(
            "Confirm: proceed to move/copy" if mode == MODE_MOVE_COPY else "Confirm: done",
            f"{report.archives_accepted} accepted, "
            f"{report.archives_hash_failed} without hash match.",
            details,
        )
        if not proceed:
            logger.log("User aborted before Phase C.")
            report.cancelled = True
            report.log_lines = logger.lines()
            return report

    # ── Phase C (move_copy only) ─────────────────────────────────────────
    if mode == MODE_MOVE_COPY:
        t_c = time.monotonic()
        mat_results = run_phase_c(
            hash_results,
            request.dest_folder,
            request.archive_action,
            request.meta_action,
            request.meta_fallback,
            request.shared_folder,
            logger=logger,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
        )
        report.phase_c_elapsed = time.monotonic() - t_c
        report.materialize_results = mat_results
        report.archives_moved_copied = sum(
            1 for r in mat_results if r.dest_archive_path
        )

        if cancel_cb and cancel_cb():
            report.cancelled = True

    # ── Final summary ────────────────────────────────────────────────────
    total_elapsed = time.monotonic() - t_global
    status = "CANCELLED" if report.cancelled else "complete"
    logger.log("")
    logger.log(
        f"Operation {status}: "
        f"A={report.phase_a_elapsed:.2f}s  "
        f"B={report.phase_b_elapsed:.2f}s  "
        + (f"C={report.phase_c_elapsed:.2f}s  " if mode == MODE_MOVE_COPY else "")
        + f"total={total_elapsed:.2f}s"
    )
    print(
        f"[downloads_analysis] {status}: "
        f"accepted={report.archives_accepted}  "
        f"failed={report.archives_hash_failed}  "
        f"moved/copied={report.archives_moved_copied}  "
        f"total={total_elapsed:.2f}s"
    )

    report.log_lines = logger.lines()
    return report


def save_report(
    report: DownloadsOperationReport,
    dest_path: str,
) -> None:
    """Write the report log lines to *dest_path*."""
    content = "\n".join(report.log_lines)
    if not content.endswith("\n"):
        content += "\n"
    with open(dest_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    report.saved_log_path = dest_path
