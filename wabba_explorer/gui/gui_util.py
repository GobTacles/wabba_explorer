"""Pure helper functions for the wabba_explorer GUI (no tkinter dependency)."""

from datetime import datetime
import json
import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..wabba_file import WabbaFile

from ..wabba.label_util import archive_label as _archive_label_impl
from ..wabba.label_util import directive_label as _directive_label_impl

_PREVIEW_MAX_CHARS = 4096
_PREVIEW_HEAD = 5   # first N items shown in modlist-json tab
_PREVIEW_TAIL = 5   # last  N items shown in modlist-json tab

_SEP = "\n\n" + "─" * 60 + "\n\n"
_WABBA_FILE_KEY = "wabba file"

# Compare old/new raw modlist bytes and print how localized JSON edits were.
json_edit_byte_compare = True


def _key_label(key: str, value) -> str:
    """Return a listbox label like 'Archives [30482]'."""
    if key == _WABBA_FILE_KEY:
        return _WABBA_FILE_KEY
    if isinstance(value, (list, dict)):
        return f"{key} [{len(value)}]"
    return key


def _archive_label(item: dict) -> str:
    """Label for an Archives entry: 'Name [Hash]'."""
    return _archive_label_impl(item)


def _directive_label(item: dict) -> str:
    """Label for a Directives entry: 'To [Hash]'."""
    return _directive_label_impl(item)


def _build_name_pattern(text: str) -> re.Pattern | None:
    """Translate a user filter string into a compiled regex for name/path fields.

    Rules:
    - ``^`` at the very start anchors the match to the beginning of the field.
    - ``*`` anywhere is a wildcard (matches any sequence of characters).
    - All other characters are treated as literals (re.escaped).
    - Match is case-insensitive.
    - Without ``^``, the pattern may match anywhere in the field value.

    Examples::

        "qt6"        → search for "qt6"  anywhere  (case-insensitive)
        "^mod"       → field must start with "mod"  (case-insensitive)
        "^start*mid" → field starts with "start", contains "mid" after it
    """
    anchored = text.startswith("^")
    raw = text[1:] if anchored else text
    pieces = raw.split("*")
    pattern = ".*".join(re.escape(p) for p in pieces)
    if anchored:
        pattern = "^" + pattern
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error:
        return None


def _item_matches(item, text: str, pattern: re.Pattern, name_field: str, hash_field: str) -> bool:
    """Return True if *item* matches the filter *text*.

    *pattern* is the pre-compiled regex for the name/path field (built once per
    filter change by the caller via :func:`_build_name_pattern`).

    The filter text is tested against two fields:
    - *hash_field*: full exact match (case-sensitive, as hashes are base64).
    - *name_field*: regex match using the pre-compiled *pattern*.
    """
    if not isinstance(item, dict):
        return text.lower() in str(item).lower()
    if item.get(hash_field, "") == text:
        return True
    name_val = item.get(name_field, "")
    return pattern.search(name_val) is not None


def _archive_item_matches(item, text: str, pattern: re.Pattern) -> bool:
    """Return True if *item* (an Archives entry) matches the filter *text*.

    Extends :func:`_item_matches` by also searching ``State.Name`` so that
    NexusDownloader entries can be filtered by their human-readable mod name
    rather than just the raw filename stored in the root ``Name`` field.
    """
    if not isinstance(item, dict):
        return text.lower() in str(item).lower()
    if item.get("Hash", "") == text:
        return True
    if pattern.search(item.get("Name", "")) is not None:
        return True
    state = item.get("State")
    if isinstance(state, dict):
        state_name = state.get("Name") or ""
        if state_name and pattern.search(state_name) is not None:
            return True
    return False


def _truncate(s: str) -> str:
    if len(s) > _PREVIEW_MAX_CHARS:
        return s[:_PREVIEW_MAX_CHARS] + f"\n… (truncated, {len(s)} chars total)"
    return s


def _preview_value(key: str, value) -> str:
    """Build a human-readable preview showing first 5 and last 5 sub-entries."""
    if isinstance(value, list):
        count = len(value)
        head = value[:_PREVIEW_HEAD]
        tail = value[max(count - _PREVIEW_TAIL, _PREVIEW_HEAD):] if count > _PREVIEW_HEAD else []

        header = (
            f"# {key}  —  list with {count} entr{'y' if count == 1 else 'ies'}"
            f"  (first {min(_PREVIEW_HEAD, count)}"
            + (f", last {len(tail)}" if tail else "")
            + ")\n\n"
        )
        parts = [_truncate(json.dumps(item, indent=2)) for item in head]
        if tail:
            skipped = count - len(head) - len(tail)
            parts.append(f"… {skipped} skipped …")
            parts += [_truncate(json.dumps(item, indent=2)) for item in tail]
        return header + _SEP.join(parts)

    if isinstance(value, dict):
        count = len(value)
        items = list(value.items())
        head = items[:_PREVIEW_HEAD]
        tail = items[max(count - _PREVIEW_TAIL, _PREVIEW_HEAD):] if count > _PREVIEW_HEAD else []

        header = (
            f"# {key}  —  object with {count} key{'s' if count != 1 else ''}"
            f"  (first {min(_PREVIEW_HEAD, count)}"
            + (f", last {len(tail)}" if tail else "")
            + ")\n\n"
        )
        parts = [_truncate(json.dumps({k: v}, indent=2)) for k, v in head]
        if tail:
            skipped = count - len(head) - len(tail)
            parts.append(f"… {skipped} skipped …")
            parts += [_truncate(json.dumps({k: v}, indent=2)) for k, v in tail]
        return header + _SEP.join(parts)

    s = _truncate(json.dumps(value, indent=2))
    return f"# {key}\n\n{s}"


def _build_wabba_file_preview(path: str, file_size: int, modified_ts: float, name: str, version: str) -> str:
    """Build synthetic 'wabba file' preview text for the main tab.

    Parameters are file path, file size in bytes, modification timestamp,
    and Name/Version values read from modlist JSON.
    """
    bytes_grouped = f"{file_size:,}".replace(",", "'")
    gib = file_size / (1024 ** 3)
    modified = datetime.fromtimestamp(modified_ts).strftime("%Y-%m-%d %H:%M:%S")
    return "\n".join(
        [
            f"# {_WABBA_FILE_KEY}",
            "",
            f"Path: {path}",
            f"Size: {bytes_grouped} bytes ({gib:.1f}GiB)",
            f"Modified: {modified}",
            f"Name: {name or 'unknown'}",
            f"Version: {version or 'unknown'}",
        ]
    )


# ---------------------------------------------------------------------------
# Inline-file extraction helpers
# ---------------------------------------------------------------------------

def _get_extract_source_id(directive: dict) -> str | None:
    """Return the wabba archive entry name for extraction, or None if not extractable.

    - ``InlineFile``          → ``SourceDataID``
    - ``RemappedInlineFile``  → ``SourceDataID``
    - ``PatchedFromArchive``  → ``PatchID``
    - Anything else           → ``None`` (disabled)
    """
    t = directive.get("$type", "")
    if t in ("InlineFile", "RemappedInlineFile"):
        return directive.get("SourceDataID") or None
    if t == "PatchedFromArchive":
        return directive.get("PatchID") or None
    return None


def _do_extract_inline(wabba: "WabbaFile", source_id: str, default_filename: str) -> None:
    """Open a Save-As dialog and write *source_id* from *wabba* to the chosen path."""
    from tkinter import filedialog, messagebox
    save_path = filedialog.asksaveasfilename(
        initialfile=default_filename,
        title="Extract InlineFile",
    )
    if not save_path:
        return
    try:
        data = wabba.read_bytes(source_id)
        with open(save_path, "wb") as fh:
            fh.write(data)
    except Exception as exc:
        messagebox.showerror("Extract InlineFile", f"Failed to extract:\n{exc}")


def _human_bytes(n: int) -> str:
    """Format bytes using requested thresholds for console messages."""
    if n < 5 * 1024:
        return f"{n} bytes"
    if n < int(0.6 * 1024 * 1024):
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"
