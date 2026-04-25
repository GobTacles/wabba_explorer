"""Phase C: move/copy archive files and handle associated .meta files.

Skipped for verify and find-one modes.  Runs only after the user has
confirmed the Phase B hash-check results.

No GUI dependency.
"""

from __future__ import annotations

import os
import time

from .downloads_analysis_types import (
    ArchiveHashResult,
    ArchiveMaterializeResult,
    CancelCallback,
    DownloadsProgressEvent,
    ProgressCallback,
)
from .downloads_analysis_fileops import (
    ReportLogger,
    check_free_space,
    os_copy_file,
    os_move_file,
    same_drive,
)


def _archive_basename(archive: dict) -> str:
    """Return the bare filename for an archive entry's Name field."""
    name = archive.get("Name", "archive")
    return name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _write_meta_text(dest_path: str, content: str, logger: ReportLogger) -> bool:
    """Write meta *content* to *dest_path*.  Returns True on success."""
    if os.path.exists(dest_path):
        logger.log(f"  ERROR: meta target already exists, not overwriting: {dest_path}")
        return False
    try:
        with open(dest_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        return True
    except Exception as exc:
        logger.log(f"  ERROR writing meta to {dest_path}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Phase C main entry point
# ---------------------------------------------------------------------------

def run_phase_c(
    hash_results: list[ArchiveHashResult],
    dest_folder: str,
    archive_action: str,      # "move" or "copy"
    meta_action: str,         # "move"/"copy"/"export"/"generate"/"skip"
    meta_fallback: str,       # "export"/"generate"/"skip"
    shared_folder: str,
    *,
    logger: "ReportLogger | None" = None,
    progress_cb: "ProgressCallback | None" = None,
    cancel_cb: "CancelCallback | None" = None,
) -> list[ArchiveMaterializeResult]:
    """Move or copy accepted archives (and optionally .meta) to *dest_folder*.

    Errors are collected and the loop continues; never raises.
    """
    t_start = time.monotonic()
    logger = logger or ReportLogger()

    logger.section(f"Phase C: {archive_action} to destination")
    logger.log(f"Destination: {dest_folder}")
    logger.log(f"Archive action: {archive_action}  Meta action: {meta_action}")

    results: list[ArchiveMaterializeResult] = []
    total = len(hash_results)
    moved_copied = 0

    for i, hr in enumerate(hash_results):
        if cancel_cb and cancel_cb():
            break

        archive = hr.archive
        name = _archive_basename(archive)
        mr = ArchiveMaterializeResult(archive=archive)

        if progress_cb:
            progress_cb(DownloadsProgressEvent(
                phase=f"Phase C: {archive_action}",
                current=i + 1,
                total=total,
                elapsed=time.monotonic() - t_start,
                current_archive_name=name,
            ))

        if hr.accepted_candidate is None:
            logger.log(f"[C] {i + 1}/{total} {name}: SKIP (no accepted candidate)")
            mr.skipped = True
            results.append(mr)
            continue

        logger.log(f"[C] {i + 1}/{total} {name}")

        src_path = hr.accepted_candidate.path
        dest_archive_path = os.path.join(dest_folder, name)

        # --- No-overwrite check ---
        if os.path.exists(dest_archive_path):
            err = f"destination file already exists, not overwriting: {dest_archive_path}"
            logger.log(f"  ERROR: {err}")
            mr.errors.append(err)
            results.append(mr)
            continue

        # --- Per-file free-space check (skip for same-drive move) ---
        if not (archive_action == "move" and same_drive(src_path, dest_folder)):
            file_size = hr.accepted_candidate.file_size
            space_err = check_free_space(dest_folder, file_size)
            if space_err:
                logger.log(f"  ERROR: {space_err}")
                mr.errors.append(space_err)
                results.append(mr)
                continue

        # --- Execute move or copy ---
        try:
            if archive_action == "move":
                os_move_file(src_path, dest_archive_path)
            else:
                os_copy_file(src_path, dest_archive_path)
            mr.dest_archive_path = dest_archive_path
            moved_copied += 1
            logger.log(f"  {archive_action}d → {dest_archive_path}")
        except Exception as exc:
            err = f"failed to {archive_action} archive: {exc}"
            logger.log(f"  ERROR: {err}")
            mr.errors.append(err)
            results.append(mr)
            continue

        # --- Handle .meta ---
        dest_meta_path = dest_archive_path + ".meta"

        if meta_action == "skip":
            pass  # nothing to do

        elif meta_action in ("move", "copy"):
            src_meta = src_path + ".meta"
            if os.path.isfile(src_meta):
                if os.path.exists(dest_meta_path):
                    logger.log(f"  ERROR: meta target already exists: {dest_meta_path}")
                    mr.errors.append(f"meta target already exists: {dest_meta_path}")
                else:
                    try:
                        if meta_action == "move":
                            os_move_file(src_meta, dest_meta_path)
                        else:
                            os_copy_file(src_meta, dest_meta_path)
                        mr.dest_meta_path = dest_meta_path
                        logger.log(f"  meta {meta_action}d → {dest_meta_path}")
                    except Exception as exc:
                        err = f"failed to {meta_action} meta: {exc}"
                        logger.log(f"  ERROR: {err}")
                        mr.errors.append(err)
            else:
                # Meta missing in shared folder – apply fallback
                logger.log(f"  meta not found in shared folder, using fallback: {meta_fallback}")
                _apply_meta_fallback(
                    archive, dest_meta_path, meta_fallback, logger, mr
                )

        elif meta_action in ("export", "generate"):
            _apply_meta_fallback(archive, dest_meta_path, meta_action, logger, mr)

        results.append(mr)

    elapsed = time.monotonic() - t_start
    logger.log("")
    logger.log(
        f"Phase C done: {moved_copied} archives {archive_action}d  ({elapsed:.2f}s)"
    )
    print(
        f"[downloads_analysis] Phase C done: "
        f"{moved_copied} {archive_action}d  ({elapsed:.2f}s)"
    )
    return results


def _apply_meta_fallback(
    archive: dict,
    dest_meta_path: str,
    fallback: str,
    logger: ReportLogger,
    mr: ArchiveMaterializeResult,
) -> None:
    """Write a .meta file via export or generate, or skip."""
    if fallback == "skip":
        return

    if fallback == "export":
        raw = archive.get("Meta", "")
        if not isinstance(raw, str) or not raw.strip():
            logger.log("  export meta: no direct Meta content, skipping")
            return
        content = raw.replace("\\n", "\n")
        if not content.endswith("\n"):
            content += "\n"
        if _write_meta_text(dest_meta_path, content, logger):
            mr.dest_meta_path = dest_meta_path
            logger.log(f"  exported meta → {dest_meta_path}")
        else:
            mr.errors.append(f"export meta failed: {dest_meta_path}")

    elif fallback == "generate":
        from .generate_meta import generate_meta
        content = generate_meta(archive)
        if content is None:
            logger.log("  generate meta: no content generated, skipping")
            return
        if _write_meta_text(dest_meta_path, content, logger):
            mr.dest_meta_path = dest_meta_path
            logger.log(f"  generated meta → {dest_meta_path}")
        else:
            mr.errors.append(f"generate meta failed: {dest_meta_path}")
