"""Pure text helpers for describing .wabbajack archive entries and directives.

These functions return plain strings or lists of strings and have no GUI
dependency, so they can be used from both the GUI and the CLI.
"""

import base64
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache import WabbaCache

from ..WabbaHash import WabbaHashXX64, WabbaHashXX64_stream

_INLINE_PREVIEW_MAX = 256 * 1024       # 256 KiB – unpack InlineFile below this
_INLINE_WABBAHASH_MAX = 128 * 1024 * 1024  # 128 MiB


# ---------------------------------------------------------------------------
# Single entry helpers
# ---------------------------------------------------------------------------

def get_wabba_entry_lines(wabba, source_id: str, label: str, compare_hash: str = "") -> list[str]:
    """Return lines describing a wabba archive entry.

    Covers: uncompressed/compressed sizes, CRC, WabbaHashXX64, and an
    optional text preview when the entry is small enough.

    *wabba* must be an open ``WabbaFile`` instance.
    Raises ``FileNotFoundError`` if *source_id* is not in the archive.
    """
    info = wabba.get_zip_info(source_id)
    lines: list[str] = [
        f"[{label}] Archive entry: {source_id}",
        f"  Uncompressed size : {info.file_size:,} bytes",
        f"  Compressed size   : {info.compress_size:,} bytes",
    ]
    crc_b64 = base64.b64encode(info.CRC.to_bytes(4, "little")).decode()
    lines.append(f"  CRC               : {info.CRC:#010x}  ({crc_b64})")

    data: bytes | None = None
    if info.file_size < _INLINE_PREVIEW_MAX:
        data = wabba.read_bytes(source_id)

    if info.file_size <= _INLINE_WABBAHASH_MAX:
        if data is not None:
            wabba_hash = WabbaHashXX64(data)
        else:
            with wabba.open_member(source_id) as stream:
                wabba_hash = WabbaHashXX64_stream(stream)
        if compare_hash:
            match_note = (
                "[matches directive Hash]"
                if wabba_hash == compare_hash
                else "[does not match directive Hash]"
            )
        else:
            match_note = "[no Hash to compare]"
        lines.append(f"  WabbaHashXX64     : {wabba_hash}  {match_note}")
    else:
        lines.append(
            "  WabbaHashXX64     : (WabbaHash for large files not yet implemented)"
        )

    if info.file_size < _INLINE_PREVIEW_MAX:
        if data is None:
            data = wabba.read_bytes(source_id)
        raw_text = data.decode("latin-1", errors="replace")
        clean = "".join(
            c if c.isprintable() or c in "\n\r\t" else "?" for c in raw_text
        )
        lines.append(f"\n--- File preview ({info.file_size:,} bytes) ---")
        lines.append(clean)

    return lines


# ---------------------------------------------------------------------------
# Directive detail
# ---------------------------------------------------------------------------

def get_directive_detail_text(item: dict, archives_by_hash: dict, wabba) -> str:
    """Return rich detail text for a single directive.

    Covers:
    - FromArchive / PatchedFromArchive: resolves the matching Archives entry.
    - InlineFile / RemappedInlineFile: shows zip-entry metadata, WabbaHash,
      and a text preview for small entries.
    - Any directive with a ``PatchID`` key: looks up the PatchID entry.

    *wabba* may be ``None`` when no archive is open; the section requiring
    a live archive handle is simply skipped.
    """
    t = item.get("$type", "")
    lines: list[str] = []

    if t in ("FromArchive", "PatchedFromArchive"):
        ahp = item.get("ArchiveHashPath")
        h = (ahp[0] if ahp else None) or item.get("Hash", "")
        if h and h in archives_by_hash:
            archive_entry = archives_by_hash[h]
            lines.append(f"[{t}] Matching Archives entry:")
            lines.append(json.dumps(archive_entry, indent=2))
        elif h:
            lines.append(f"[{t}] Hash '{h}' not found in Archives")

    elif t in ("InlineFile", "RemappedInlineFile") and wabba is not None:
        source_id = item.get("SourceDataID", "")
        if source_id:
            try:
                lines.extend(
                    get_wabba_entry_lines(
                        wabba, source_id, t, compare_hash=item.get("Hash", "") or ""
                    )
                )
            except FileNotFoundError:
                lines.append(f"[{t}] Source file '{source_id}' not found in archive")

    # PatchID – present on any directive type, most commonly PatchedFromArchive
    patch_id = item.get("PatchID", "")
    if patch_id and wabba is not None:
        if lines:
            lines.append("")  # blank separator
        try:
            lines.extend(get_wabba_entry_lines(wabba, patch_id, "PatchID"))
        except FileNotFoundError:
            lines.append(f"[PatchID] '{patch_id}' not found in wabba archive")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Archive cross-reference
# ---------------------------------------------------------------------------

def get_archive_directives_text(
    archive_item: dict,
    all_directives: list,
    cache: "WabbaCache | None" = None,
) -> str:
    """Return text listing all directives that reference *archive_item* by hash.

    When *cache* is provided and ``cache.archive_to_directives`` is populated
    (set during ``run_archives_prep``) a direct O(1) dict lookup is used
    instead of scanning all 300k+ directives linearly.  When *cache* is
    absent or the index is empty, the function falls back to the linear scan
    over *all_directives* for backward compatibility (CLI, tests, etc.).

    Returns an empty string when no directives reference the archive.
    """
    archive_hash = archive_item.get("Hash", "")
    if not archive_hash:
        return ""

    # Fast path: use pre-built index from WabbaCache.
    atd = getattr(cache, "archive_to_directives", None)
    if atd:
        matches = atd.get(archive_hash, [])
    else:
        # Fallback: linear scan (kept for CLI / test / legacy callers).
        matches = []
        for d in all_directives:
            if not isinstance(d, dict):
                continue
            ahp = d.get("ArchiveHashPath")
            lookup_hash = (ahp[0] if ahp else None) or d.get("Hash", "")
            if lookup_hash == archive_hash:
                matches.append(d)

    total = len(matches)
    if total == 0:
        return ""

    def _fmt(d: dict) -> str:
        to = d.get("To", "(no To)")
        h = d.get("Hash", "")
        return f"  {to} [{h}]" if h else f"  {to}"

    lines = [f"{total} directive(s) using this archive:"]
    if total <= 10:
        for d in matches:
            lines.append(_fmt(d))
    else:
        for d in matches[:5]:
            lines.append(_fmt(d))
        lines.append(f"  … ({total - 10} more) …")
        for d in matches[-5:]:
            lines.append(_fmt(d))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Filesystem-tree node preview
# ---------------------------------------------------------------------------

def get_node_preview_lines(
    path: str,
    all_directives: list[tuple[str, dict]],
    wabba,
    archives_by_hash: dict,
) -> list[str]:
    """Return preview lines for a filesystem-tree node at *path*.

    *all_directives* is a list of ``(norm_path, directive_dict)`` pairs as
    built by ``_FsTreePanel.load_directives``.
    *wabba* may be ``None``; archive-entry details are skipped in that case.
    *archives_by_hash* maps hash strings to archive entry dicts.
    """
    # Affecting directives: exact match, ancestor, or descendant
    affecting = [
        (norm, d)
        for norm, d in all_directives
        if norm == path
        or path.startswith(norm + "/")
        or norm.startswith(path + "/")
    ]
    total = len(affecting)

    lines: list[str] = [f"{total} Directive(s) affecting \"{path}\" :", ""]
    if total == 0:
        lines.append("(none)")
    elif total <= 10:
        for _, d in affecting:
            lines.append(f"{d.get('To', '?')} [{d.get('Hash', '?')}]")
    else:
        for _, d in affecting[:5]:
            lines.append(f"{d.get('To', '?')} [{d.get('Hash', '?')}]")
        lines.append(f"... [{total} total]")
        for _, d in affecting[-5:]:
            lines.append(f"{d.get('To', '?')} [{d.get('Hash', '?')}]")

    if not affecting:
        return lines

    _, last_d = affecting[-1]
    lines.append("")
    lines.append(json.dumps(last_d, indent=2))

    last_type = last_d.get("$type", "")

    if last_type in ("InlineFile", "RemappedInlineFile") and wabba is not None:
        source_id = last_d.get("SourceDataID", "")
        if source_id:
            lines.append("")
            try:
                entry_lines = get_wabba_entry_lines(
                    wabba,
                    source_id,
                    last_type,
                    compare_hash=(
                        last_d.get("Hash", "")
                        if isinstance(last_d.get("Hash", ""), str)
                        else ""
                    ),
                )
                # Rewrite the match note to use the legacy wording from the
                # original FsTreePanel for consistency with existing output.
                entry_lines = [
                    line.replace(
                        "[matches directive Hash]",
                        "[matches last directive Hash]",
                    ).replace(
                        "[does not match directive Hash]",
                        "[does not match last directive Hash]",
                    )
                    for line in entry_lines
                ]
                lines.extend(entry_lines)
            except FileNotFoundError:
                lines.append(
                    f"[{last_type}] Source file '{source_id}' not found in archive"
                )

    if last_type == "FromArchive":
        archive_hash_path = last_d.get("ArchiveHashPath")
        h = (archive_hash_path[0] if archive_hash_path else None) or last_d.get("Hash", "")
        if h and h in archives_by_hash:
            archive_entry = archives_by_hash[h]
            lines.append("")
            lines.append("[FromArchive] Matching Archives entry:")
            lines.append(json.dumps(archive_entry, indent=2))
        elif h:
            lines.append("")
            lines.append(f"[FromArchive] Hash '{h}' not found in Archives")

    if last_type == "PatchedFromArchive":
        archive_hash_path = last_d.get("ArchiveHashPath")
        h = (archive_hash_path[0] if archive_hash_path else None) or last_d.get("Hash", "")
        if h and h in archives_by_hash:
            archive_entry = archives_by_hash[h]
            lines.append("")
            lines.append("[PatchedFromArchive] Matching Archives entry:")
            lines.append(json.dumps(archive_entry, indent=2))
        elif h:
            lines.append("")
            lines.append(f"[PatchedFromArchive] Hash '{h}' not found in Archives")
        patch_id = last_d.get("PatchID", "")
        if patch_id and wabba is not None:
            lines.append("")
            try:
                lines.extend(get_wabba_entry_lines(wabba, patch_id, "PatchID"))
            except FileNotFoundError:
                lines.append(f"[PatchID] '{patch_id}' not found in wabba archive")

    return lines
