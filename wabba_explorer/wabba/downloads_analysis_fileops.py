"""Filesystem utilities for the downloads-analysis workflow.

All helpers here are OS-agnostic in interface but use OS-level APIs for
file operations (shutil.copy2 / os.rename / os.replace) rather than custom
chunked read/write loops.  No GUI dependency.
"""

from __future__ import annotations

import os
import shutil
import time
from datetime import datetime


# ---------------------------------------------------------------------------
# Free-space helpers
# ---------------------------------------------------------------------------

def get_free_space(path: str) -> int:
    """Return available free bytes on the volume that contains *path*.

    Uses os.statvfs on POSIX and shutil.disk_usage on Windows.
    Falls back to 0 on error so callers treat any check failure conservatively.
    """
    try:
        usage = shutil.disk_usage(path)
        return usage.free
    except Exception:
        return 0


def same_drive(path_a: str, path_b: str) -> bool:
    """Return True if both absolute paths are on the same drive / mount point."""
    try:
        return os.path.splitdrive(os.path.abspath(path_a))[0].lower() == \
               os.path.splitdrive(os.path.abspath(path_b))[0].lower()
    except Exception:
        return False


def check_free_space(dest_folder: str, required_bytes: int) -> str:
    """Return an error string if *dest_folder* lacks *required_bytes* of free space.

    Returns an empty string when space is sufficient.
    """
    free = get_free_space(dest_folder)
    if free < required_bytes:
        free_mb = free / (1024 * 1024)
        req_mb = required_bytes / (1024 * 1024)
        return (
            f"Not enough free space in destination: "
            f"{free_mb:.1f} MiB available, {req_mb:.1f} MiB required."
        )
    return ""


# ---------------------------------------------------------------------------
# Safe move / copy
# ---------------------------------------------------------------------------

def os_copy_file(src: str, dst: str) -> None:
    """Copy *src* to *dst* using shutil.copy2 (metadata-preserving OS copy).

    Raises on any error.  The destination directory must already exist.
    *dst* must not already exist (caller is responsible for the no-overwrite
    check).
    """
    shutil.copy2(src, dst)


def os_move_file(src: str, dst: str) -> None:
    """Move *src* to *dst*.

    Strategy:
    - Same drive: os.rename (atomic on most OS).
    - Different drive: shutil.copy2 then os.remove on success.

    Raises on any error.  *dst* must not exist.
    """
    if same_drive(src, dst):
        os.rename(src, dst)
    else:
        # Cross-drive: copy first, then delete source.
        shutil.copy2(src, dst)
        try:
            os.remove(src)
        except Exception as exc:
            # Copy succeeded but delete failed; log and leave the copy in place.
            raise RuntimeError(
                f"Cross-drive move: file was copied to '{dst}' but "
                f"removing source '{src}' failed: {exc}"
            ) from exc


# ---------------------------------------------------------------------------
# Log-file naming
# ---------------------------------------------------------------------------

def make_log_filename(prefix: str = "wabbaexplorer-move") -> str:
    """Return a timestamped log filename like wabbaexplorer-move-20260425-142233.log."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}.log"


# ---------------------------------------------------------------------------
# Structured report logger
# ---------------------------------------------------------------------------

class ReportLogger:
    """Collects report lines in memory, mirrors them to an optional console callback."""

    def __init__(self, log_callback: "callable | None" = None) -> None:
        self._lines: list[str] = []
        self._log_callback = log_callback
        self._t0 = time.monotonic()

    def log(self, line: str) -> None:
        self._lines.append(line)
        if self._log_callback is not None:
            self._log_callback(line)

    def section(self, title: str) -> None:
        sep = "─" * 60
        self.log("")
        self.log(sep)
        self.log(title)
        self.log(sep)

    def elapsed(self) -> float:
        return time.monotonic() - self._t0

    def log_elapsed(self, label: str) -> float:
        e = self.elapsed()
        self.log(f"{label}: {e:.2f}s")
        return e

    def lines(self) -> list[str]:
        return list(self._lines)

    def text(self) -> str:
        return "\n".join(self._lines)

    def save(self, path: str) -> None:
        """Write collected lines to *path* (UTF-8, LF line endings)."""
        content = "\n".join(self._lines)
        if not content.endswith("\n"):
            content += "\n"
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
