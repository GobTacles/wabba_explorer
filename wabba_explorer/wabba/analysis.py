"""Pure analysis loop for .wabbajack directive validation.

This module has no GUI dependency and can be driven from the CLI or the GUI
via a callback.
"""

import time
from dataclasses import dataclass, field

from ..WabbaHash import WabbaHashXX64_stream

_PROBLEMS_UPDATE_INTERVAL_SECS = 2.0

# Root-level wabba zip entries that are not inline-file UUIDs and should be
# excluded from the "Unused InlineFiles" report.
_WABBA_ROOT_SYSTEM_FILES = {"modlist", "modlist-image.png"}


@dataclass
class AnalysisResult:
    """Snapshot of analysis progress / final results."""

    total: int = 0
    processed: int = 0
    matches: int = 0
    mismatches: int = 0
    ignores: int = 0
    elapsed: float = 0.0
    mismatch_directives: list = field(default_factory=list)
    unused_archives: list = field(default_factory=list)
    missing_archives: list = field(default_factory=list)
    missing_inline_files: list = field(default_factory=list)
    unused_inline_files: list = field(default_factory=list)


def analyze_directives(
    directives: list,
    archives: list,
    wabba,
    *,
    should_stop=None,
    on_progress=None,
    update_interval_secs: float = _PROBLEMS_UPDATE_INTERVAL_SECS,
    precomputed_archive_hashes: "set[str] | None" = None,
    precomputed_root_names: "set[str] | None" = None,
) -> "AnalysisResult":
    """Iterate *directives* and validate hashes / archive references.

    Parameters
    ----------
    directives:
        List of directive dicts from the modlist ``Directives`` key.
    archives:
        List of archive dicts from the modlist ``Archives`` key.
        Used only for computing ``unused_archives`` in the result.
    wabba:
        An open ``WabbaFile`` instance (or ``None`` to skip hash checks).
    should_stop:
        Optional ``callable() -> bool``.  Called before each directive; when
        it returns ``True`` the loop terminates early and returns the partial
        ``AnalysisResult`` collected so far.
    on_progress:
        Optional ``callable(result: AnalysisResult, done: bool)``.  Called
        periodically during the loop and once more when *done* is ``True``.
    update_interval_secs:
        Minimum wall-clock seconds between ``on_progress`` calls.
    precomputed_archive_hashes:
        Optional pre-built set of archive hash strings (from
        ``WabbaCache.archives_by_hash``).  When provided the function skips
        rebuilding this set from *archives*.
    precomputed_root_names:
        Optional pre-built set of wabba zip root filenames (from
        ``WabbaCache.wabba_root_names``).  When provided the function skips
        calling ``wabba.list_root_files()``.

    Returns
    -------
    AnalysisResult
        The final (or partial, if stopped early) analysis result.
    """
    total = len(directives)
    processed = 0
    matches = 0
    mismatches = 0
    ignores = 0
    mismatch_directives: list[dict] = []
    used_hashes: set[str] = set()
    missing_archives: list[str] = []
    missing_inline_files: list[str] = []
    referenced_inline_ids: set[str] = set()

    archive_hash_set: set[str]
    if precomputed_archive_hashes is not None:
        archive_hash_set = precomputed_archive_hashes
    else:
        archive_hash_set = {
            a.get("Hash", "") for a in archives if isinstance(a, dict) and a.get("Hash", "")
        }
    wabba_root_names: set[str]
    if precomputed_root_names is not None:
        wabba_root_names = precomputed_root_names
    else:
        wabba_root_names = set()
        if wabba is not None:
            try:
                wabba_root_names = set(wabba.list_root_files())
            except Exception:
                pass

    start = time.monotonic()
    last_update = start

    for d in directives:
        if should_stop is not None and should_stop():
            break

        processed += 1

        if not isinstance(d, dict):
            ignores += 1
        else:
            dtype = d.get("$type", "")

            if dtype in ("FromArchive", "PatchedFromArchive"):
                ahp = d.get("ArchiveHashPath")
                h = (ahp[0] if ahp else None) or d.get("Hash", "")
                if h:
                    used_hashes.add(h)

            if dtype == "InlineFile":
                expected_hash = d.get("Hash", "")
                source_id = d.get("SourceDataID", "")
                actual_hash = ""
                if wabba is not None and isinstance(source_id, str) and source_id:
                    try:
                        with wabba.open_member(source_id) as stream:
                            actual_hash = WabbaHashXX64_stream(stream)
                    except FileNotFoundError:
                        actual_hash = ""
                if expected_hash and actual_hash and expected_hash == actual_hash:
                    matches += 1
                else:
                    mismatches += 1
                    mismatch_directives.append(d)
                if isinstance(source_id, str) and source_id:
                    referenced_inline_ids.add(source_id)
                    if source_id not in wabba_root_names:
                        to = d.get("To", source_id)
                        missing_inline_files.append(
                            f"- missing InlineFile: {to} [SourceDataID={source_id}]"
                        )

            elif dtype == "RemappedInlineFile":
                source_id = d.get("SourceDataID", "")
                if isinstance(source_id, str) and source_id:
                    referenced_inline_ids.add(source_id)
                    if source_id not in wabba_root_names:
                        to = d.get("To", source_id)
                        missing_inline_files.append(
                            f"- missing InlineFile: {to} [SourceDataID={source_id}]"
                        )
                ignores += 1

            elif dtype == "FromArchive":
                ahp = d.get("ArchiveHashPath")
                h = (ahp[0] if ahp else None) or d.get("Hash", "")
                if h and h not in archive_hash_set:
                    to = d.get("To", "?")
                    missing_archives.append(f"- missing Archive: {to} [hash={h}]")
                ignores += 1

            elif dtype == "PatchedFromArchive":
                ahp = d.get("ArchiveHashPath")
                h = (ahp[0] if ahp else None) or d.get("Hash", "")
                if h and h not in archive_hash_set:
                    to = d.get("To", "?")
                    missing_archives.append(f"- missing Archive: {to} [hash={h}]")
                patch_id = d.get("PatchID", "")
                if isinstance(patch_id, str) and patch_id:
                    referenced_inline_ids.add(patch_id)
                    if patch_id not in wabba_root_names:
                        to = d.get("To", patch_id)
                        missing_inline_files.append(
                            f"- missing InlineFile: {to} [PatchID={patch_id}]"
                        )
                ignores += 1

            else:
                ignores += 1

        now = time.monotonic()
        if on_progress is not None and now - last_update >= update_interval_secs:
            elapsed = now - start
            on_progress(
                AnalysisResult(
                    total=total,
                    processed=processed,
                    matches=matches,
                    mismatches=mismatches,
                    ignores=ignores,
                    elapsed=elapsed,
                    mismatch_directives=list(mismatch_directives),
                ),
                False,
            )
            last_update = now

    elapsed = time.monotonic() - start
    unused_archives = [
        a for a in archives
        if isinstance(a, dict) and a.get("Hash", "") not in used_hashes
    ]
    unused_inline_files = sorted(wabba_root_names - referenced_inline_ids - _WABBA_ROOT_SYSTEM_FILES)

    result = AnalysisResult(
        total=total,
        processed=processed,
        matches=matches,
        mismatches=mismatches,
        ignores=ignores,
        elapsed=elapsed,
        mismatch_directives=list(mismatch_directives),
        unused_archives=unused_archives,
        missing_archives=list(missing_archives),
        missing_inline_files=list(missing_inline_files),
        unused_inline_files=unused_inline_files,
    )

    if on_progress is not None:
        on_progress(result, True)

    return result
