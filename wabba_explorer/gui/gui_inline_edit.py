"""InlineFile edit workflow helpers and progress dialog UI."""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import threading
import time
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from collections import Counter
from typing import TYPE_CHECKING, Callable

from ..WabbaHash import WabbaHashXX64

if TYPE_CHECKING:
    from ..wabba_file import WabbaFile


# Compare old/new raw modlist bytes and print how localized JSON edits were.
json_edit_byte_compare = True


def _human_bytes(n: int) -> str:
    """Format bytes using requested thresholds for console messages."""
    if n < 5 * 1024:
        return f"{n} bytes"
    if n < int(0.6 * 1024 * 1024):
        return f"{n / 1024:.1f} KiB"
    return f"{n / (1024 * 1024):.1f} MiB"


def _human_elapsed(seconds: float) -> str:
    """Format elapsed seconds for final user-visible status messages."""
    if seconds < 1.0:
        return f"{seconds:.2f}s"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    rem = seconds - (minutes * 60)
    return f"{minutes}m {rem:.1f}s"


def _matching_edge_bytes(old: bytes, new: bytes) -> tuple[int, int]:
    """Return equal-prefix and equal-suffix byte counts without overlap."""
    i = 0
    max_prefix = min(len(old), len(new))
    while i < max_prefix and old[i] == new[i]:
        i += 1

    j = 0
    old_i = len(old) - 1
    new_i = len(new) - 1
    while old_i - j >= i and new_i - j >= i and old[old_i - j] == new[new_i - j]:
        j += 1
    return i, j


def _collect_json_object_spans(text: str) -> list[tuple[int, int]]:
    """Collect all JSON object spans as (start, end_exclusive), string-aware."""
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    in_string = False
    escaped = False

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            spans.append((start, i + 1))

    return spans


def _smallest_span_containing(spans: list[tuple[int, int]], pos: int) -> tuple[int, int] | None:
    """Find the smallest span containing *pos* from pre-collected spans."""
    best = None
    best_len = None
    for start, end in spans:
        if start <= pos < end:
            cur_len = end - start
            if best is None or (best_len is not None and cur_len < best_len):
                best = (start, end)
                best_len = cur_len
    return best


def _patch_inline_directive_in_modlist_raw(
    raw_modlist: bytes,
    directive: dict,
    new_hash: str,
    new_size: int,
) -> bytes:
    """Patch only Hash/Size in the target InlineFile directive in raw modlist bytes."""
    try:
        text = raw_modlist.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"modlist is not valid UTF-8: {exc}") from exc

    source_id = directive.get("SourceDataID", "")
    to_path = directive.get("To", "")
    if directive.get("$type") != "InlineFile" or not source_id or not to_path:
        raise ValueError("Selected directive is not a valid InlineFile with SourceDataID/To.")

    source_token = json.dumps(str(source_id))[1:-1]
    to_token = json.dumps(str(to_path))[1:-1]
    old_hash = str(directive.get("Hash", ""))
    old_size = directive.get("Size")

    source_rx = re.compile(r'"SourceDataID"\s*:\s*"' + re.escape(source_token) + r'"')
    to_rx = re.compile(r'"To"\s*:\s*"' + re.escape(to_token) + r'"')
    type_rx = re.compile(r'"\$type"\s*:\s*"InlineFile"')
    old_hash_rx = re.compile(r'"Hash"\s*:\s*"' + re.escape(old_hash) + r'"') if old_hash else None
    old_size_rx = (
        re.compile(r'"Size"\s*:\s*' + re.escape(str(old_size)) + r'(?!\d)')
        if old_size is not None
        else None
    )

    spans = _collect_json_object_spans(text)
    candidates: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()

    for match in source_rx.finditer(text):
        span = _smallest_span_containing(spans, match.start())
        if span is None or span in seen:
            continue
        seen.add(span)
        start, end = span
        obj = text[start:end]
        if not type_rx.search(obj):
            continue
        if not to_rx.search(obj):
            continue
        if old_hash_rx is not None and not old_hash_rx.search(obj):
            continue
        if old_size_rx is not None and not old_size_rx.search(obj):
            continue
        candidates.append(span)

    if len(candidates) != 1:
        raise ValueError(
            f"Could not uniquely locate target InlineFile directive in raw modlist (matches={len(candidates)})."
        )

    start, end = candidates[0]
    obj = text[start:end]
    escaped_new_hash = json.dumps(new_hash)[1:-1]

    obj_after_hash, hash_n = re.subn(
        r'("Hash"\s*:\s*)"(?:[^"\\]|\\.)*"',
        r'\g<1>"' + escaped_new_hash + '"',
        obj,
        count=1,
    )
    if hash_n != 1:
        raise ValueError("Failed to patch directive Hash in raw modlist text.")

    obj_after_size, size_n = re.subn(
        r'("Size"\s*:\s*)-?\d+',
        r'\g<1>' + str(new_size),
        obj_after_hash,
        count=1,
    )
    if size_n != 1:
        raise ValueError("Failed to patch directive Size in raw modlist text.")

    patched = text[:start] + obj_after_size + text[end:]
    return patched.encode("utf-8")


def _find_source_id_references(all_directives: list, selected: dict, source_id: str) -> list[str]:
    """Return lines for directives (except selected) that reference *source_id*."""
    lines: list[str] = []
    for d in all_directives:
        if not isinstance(d, dict):
            continue
        if d is selected:
            continue
        used_key = None
        if d.get("SourceDataID", "") == source_id:
            used_key = "SourceDataID"
        elif d.get("PatchID", "") == source_id:
            used_key = "PatchID"
        if not used_key:
            continue
        d_type = d.get("$type", "?")
        to_path = d.get("To", "(no To)")
        lines.append(f"- {d_type}: {to_path} [{used_key}={source_id}]")
    return lines


class _ReplaceInlineProgressDialog:
    """Small modal progress dialog for InlineFile replacement."""

    def __init__(self, root: tk.Misc) -> None:
        self._win = tk.Toplevel(root)
        self._win.title("Replace InlineFile")
        self._win.transient(root)
        self._win.grab_set()
        self._win.resizable(False, False)
        self._win.protocol("WM_DELETE_WINDOW", lambda: None)

        frame = ttk.Frame(self._win, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        self._status_var = tk.StringVar(value="preparing edit...")
        ttk.Label(frame, textvariable=self._status_var, anchor=tk.W, width=70).pack(fill=tk.X)
        self._pb = ttk.Progressbar(frame, mode="indeterminate", length=520)
        self._pb.pack(fill=tk.X, pady=(8, 0))
        self._pb.start(12)
        self._last_progress_apply = 0.0
        self._last_phase = ""

    def set_status(self, text: str) -> None:
        self._status_var.set(text)
        self._win.update_idletasks()

    def apply_progress_event(self, ev: dict, *, force: bool = False) -> None:
        now = time.monotonic()
        phase = str(ev.get("phase", ""))
        if not force and phase == self._last_phase and now - self._last_progress_apply < 1.0:
            return

        total = int(ev.get("bytes_total", 0) or 0)
        done = int(ev.get("bytes_done", 0) or 0)
        msg = str(ev.get("message", "Working..."))
        self._status_var.set(msg)
        if total > 0:
            if str(self._pb.cget("mode")) != "determinate":
                self._pb.stop()
                self._pb.configure(mode="determinate")
            self._pb.configure(maximum=total, value=min(done, total))
        else:
            if str(self._pb.cget("mode")) != "indeterminate":
                self._pb.configure(mode="indeterminate")
                self._pb.start(12)

        self._last_phase = phase
        self._last_progress_apply = now

    def after(self, delay_ms: int, callback) -> None:
        self._win.after(delay_ms, callback)

    def close(self) -> None:
        try:
            self._win.grab_release()
        except Exception:
            pass
        try:
            self._pb.stop()
        except Exception:
            pass
        self._win.destroy()


def _flatten_summary_for_console(text: str) -> str:
    """Flatten multiline summary text into one console-friendly line."""
    return text.replace("\n\n", " | ").replace("\n", " | ")


def _default_wabba_save_as_path(current_wabba_path: str) -> str:
    """Return '<oldname>-new.wabbajack' in the same directory."""
    folder = os.path.dirname(current_wabba_path)
    stem = os.path.splitext(os.path.basename(current_wabba_path))[0]
    return os.path.join(folder, f"{stem}-new.wabbajack")


def _ask_wabba_save_as_path(current_wabba_path: str) -> str:
    """Show Save-As dialog for writing a new .wabbajack destination path."""
    suggested = _default_wabba_save_as_path(current_wabba_path)
    return filedialog.asksaveasfilename(
        title="Save Wabbajack As",
        defaultextension=".wabbajack",
        filetypes=[("Wabbajack archives", "*.wabbajack"), ("All files", "*.*")],
        initialdir=os.path.dirname(suggested),
        initialfile=os.path.basename(suggested),
    )


def _prompt_replace_inline_action(root: tk.Misc) -> str:
    """Ask user whether to apply now, save-as now, or keep queued."""
    return _prompt_queued_action(
        root,
        title="Replace InlineFile",
        message="Change was queued. What do you want to do now?",
    )


def _prompt_queued_action(
    root: tk.Misc,
    *,
    title: str,
    message: str,
) -> str:
    """Prompt for apply/save-as/queue after adding a queued edit operation."""
    result = {"value": "queue"}

    win = tk.Toplevel(root)
    win.title(title)
    win.transient(root)
    win.grab_set()
    win.resizable(False, False)

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        frame,
        text=message,
        anchor=tk.W,
    ).pack(fill=tk.X)

    buttons = ttk.Frame(frame)
    buttons.pack(fill=tk.X, pady=(10, 0))

    def _choose(action: str) -> None:
        result["value"] = action
        win.destroy()

    ttk.Button(
        buttons,
        text="apply/write now",
        command=lambda: _choose("apply_now"),
    ).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(
        buttons,
        text="save now as...",
        command=lambda: _choose("save_as_now"),
    ).pack(side=tk.LEFT, padx=(0, 6))
    ttk.Button(
        buttons,
        text="queue changes for later",
        command=lambda: _choose("queue"),
    ).pack(side=tk.LEFT)

    win.protocol("WM_DELETE_WINDOW", lambda: _choose("queue"))
    win.wait_window()
    return str(result["value"])


def _new_uuid_filename() -> str:
    """Generate UUID4 string used for SourceDataID and ZIP root payload filename."""
    return str(uuid.uuid4())


def _directive_snapshot(directive: dict) -> dict:
    """Create a stable matching snapshot for a directive object."""
    out = {
        "$type": directive.get("$type", ""),
        "To": directive.get("To", ""),
    }
    for key in ("Hash", "Size", "SourceDataID", "PatchID", "ArchiveHashPath"):
        if key in directive:
            out[key] = directive.get(key)
    return out


def _directive_matches_snapshot(candidate: dict, snapshot: dict) -> bool:
    """Return True when *candidate* matches all fields from *snapshot*."""
    for key, value in snapshot.items():
        if candidate.get(key) != value:
            return False
    return True


def _find_directive_index(directives: list, snapshot: dict) -> int:
    """Find unique matching directive index by snapshot, else raise."""
    matches: list[int] = []
    for i, item in enumerate(directives):
        if isinstance(item, dict) and _directive_matches_snapshot(item, snapshot):
            matches.append(i)
    if len(matches) != 1:
        raise ValueError(
            "Could not uniquely match directive snapshot "
            f"(matches={len(matches)}, type={snapshot.get('$type', '')}, to={snapshot.get('To', '')})."
        )
    return matches[0]


def _build_inline_delete_change(directive: dict) -> dict:
    """Build queued delete operation for one InlineFile directive and payload."""
    source_id = str(directive.get("SourceDataID", "") or "")
    to_path = str(directive.get("To", "") or "")
    return {
        "op": "delete-inline",
        "queue_key": f"delete::{source_id}::{to_path}",
        "source_id": source_id,
        "to_path": to_path,
        "directive_snapshot": _directive_snapshot(directive),
        "display": {
            "summary": f"DELETE InlineFile: {to_path}",
            "details": [
                f"- Remove InlineFile directive '{to_path}'",
                f"- Remove wabba payload '{source_id}'",
            ],
        },
    }


def _build_inline_add_change(
    to_path: str,
    source_file_path: str,
    source_bytes: bytes,
) -> dict:
    """Build queued add operation for a new InlineFile directive + payload."""
    source_id = _new_uuid_filename()
    to_path = to_path.replace("/", "\\")
    new_hash = WabbaHashXX64(source_bytes)
    new_size = len(source_bytes)
    new_directive = {
        "$type": "InlineFile",
        "Hash": new_hash,
        "Size": new_size,
        "SourceDataID": source_id,
        "To": to_path,
    }
    return {
        "op": "add-inline",
        "queue_key": f"add::{to_path}",
        "source_id": source_id,
        "to_path": to_path,
        "new_data": source_bytes,
        "replacement_path": source_file_path,
        "new_directive": new_directive,
        "display": {
            "summary": f"ADD InlineFile: {to_path}",
            "details": [
                f"- Add InlineFile directive '{to_path} [{new_hash}]'",
                f"- Add wabba payload '{source_id}'",
                f"- Source file '{source_file_path}'",
            ],
        },
    }


def _build_convert_fromarchive_to_inline_change(
    directive: dict,
    source_file_path: str,
    source_bytes: bytes,
) -> dict:
    """Build queued conversion operation: FromArchive -> InlineFile."""
    to_path = str(directive.get("To", "") or "")
    source_id = _new_uuid_filename()
    new_hash = WabbaHashXX64(source_bytes)
    new_size = len(source_bytes)
    new_directive = {
        "$type": "InlineFile",
        "Hash": new_hash,
        "Size": new_size,
        "SourceDataID": source_id,
        "To": to_path,
    }
    return {
        "op": "convert-fromarchive-to-inline",
        "queue_key": f"convert::{to_path}",
        "source_id": source_id,
        "to_path": to_path,
        "new_data": source_bytes,
        "replacement_path": source_file_path,
        "from_snapshot": _directive_snapshot(directive),
        "new_directive": new_directive,
        "display": {
            "summary": f"CONVERT FromArchive -> InlineFile: {to_path}",
            "details": [
                f"- Remove FromArchive directive '{to_path}'",
                f"- Add InlineFile directive '{to_path} [{new_hash}]'",
                f"- Add wabba payload '{source_id}'",
                f"- Source file '{source_file_path}'",
            ],
        },
    }


def _build_inline_replacement_change(
    directive: dict,
    replacement_path: str,
    replacement_data: bytes,
) -> dict:
    """Build one queued InlineFile replacement change object."""
    source_id = str(directive.get("SourceDataID", "") or "")
    to_path = str(directive.get("To", "") or "")
    new_hash = WabbaHashXX64(replacement_data)
    new_size = len(replacement_data)
    return {
        "queue_key": f"{source_id}::{to_path}",
        "op": "replace-inline",
        "source_id": source_id,
        "to_path": to_path,
        "replacement_path": replacement_path,
        "replacement_name": os.path.basename(replacement_path),
        "new_hash": new_hash,
        "new_size": new_size,
        "new_data": replacement_data,
        # Snapshot fields used by raw-modlist patch lookup.
        "directive_snapshot": {
            "$type": directive.get("$type", ""),
            "SourceDataID": source_id,
            "To": to_path,
            "Hash": str(directive.get("Hash", "") or ""),
            "Size": directive.get("Size"),
        },
        "display": {
            "summary": f"REPLACE InlineFile: {to_path}",
            "details": [
                f"- Edit InlineFile directive '{to_path} [{new_hash}]'",
                f"- Replace wabba payload '{source_id}'",
                f"- Source file '{replacement_path}'",
            ],
        },
    }


def _read_local_file_bytes(path: str) -> bytes:
    """Read selected local file bytes with consistent user-facing error."""
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as exc:
        raise RuntimeError(f"Failed to read replacement file:\n{exc}") from exc


def _select_local_file(*, title: str, ext_hint: str = "") -> str:
    """Open file picker for local payload file selection."""
    ext = (ext_hint or "").lower()
    filetypes = [
        (f"{ext} files", f"*{ext}"),
        ("All files", "*.*"),
    ] if ext.startswith(".") else [("All files", "*.*")]
    return filedialog.askopenfilename(title=title, filetypes=filetypes)


def _prompt_inline_to_path(*, initial_to_path: str) -> str | None:
    """Prompt for full InlineFile To path and return normalized value."""
    value = simpledialog.askstring(
        "Add InlineFile with path",
        "Enter full destination path (including filename):",
        initialvalue=initial_to_path,
    )
    if value is None:
        return None
    out = value.strip().replace("/", "\\")
    if not out:
        return ""
    return out


def _archive_hash_from_directive(directive: dict) -> str:
    """Return archive hash referenced by FromArchive/PatchedFromArchive directive."""
    ahp = directive.get("ArchiveHashPath")
    return str((ahp[0] if isinstance(ahp, list) and ahp else None) or directive.get("Hash", "") or "")


def _collect_archive_affected_directives(archive_item: dict, all_directives: list) -> tuple[list[dict], Counter, str]:
    """Collect directives referencing an archive by ArchiveHashPath[0]."""
    archive_hash = str(archive_item.get("Hash", "") or "")
    matches: list[dict] = []
    counts: Counter = Counter()
    lines: list[str] = []
    for item in all_directives:
        if not isinstance(item, dict):
            continue
        if _archive_hash_from_directive(item) != archive_hash:
            continue
        matches.append(item)
        dtype = str(item.get("$type", "") or "(none)")
        counts[dtype] += 1
        lines.append(f"[{dtype}] {item.get('To', '(no To)')}")
    return matches, counts, "\n".join(lines)


def _confirm_remove_archive_change(root: tk.Misc, archive_item: dict, counts: Counter, affected_text: str) -> bool:
    """Ask for confirmation before queueing archive removal, with counts and readonly affected list."""
    result = {"ok": False}
    win = tk.Toplevel(root)
    win.title("Remove archive and directives")
    win.transient(root)
    win.grab_set()
    win.resizable(True, True)
    win.minsize(760, 420)

    frame = ttk.Frame(win, padding=10)
    frame.pack(fill=tk.BOTH, expand=True)

    archive_name = str(archive_item.get("Name", "") or archive_item.get("Hash", "") or "archive")
    ttk.Label(
        frame,
        text=f"Remove archive '{archive_name}' and all directives using it?",
        anchor=tk.W,
    ).pack(fill=tk.X)

    summary_parts = [f"{counts[key]} {key}" for key in sorted(counts)] or ["0 directives"]
    ttk.Label(
        frame,
        text="Affected directives by type: " + ", ".join(summary_parts),
        anchor=tk.W,
    ).pack(fill=tk.X, pady=(8, 4))

    ttk.Label(frame, text="Affected directive To paths:", anchor=tk.W).pack(fill=tk.X)
    text_frame = ttk.Frame(frame)
    text_frame.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
    text = tk.Text(text_frame, wrap=tk.WORD, state=tk.NORMAL, font=("Consolas", 9))
    ysb = ttk.Scrollbar(text_frame, command=text.yview)
    text.configure(yscrollcommand=ysb.set)
    ysb.pack(side=tk.RIGHT, fill=tk.Y)
    text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    text.insert(tk.END, affected_text or "(none)")
    text.configure(state=tk.DISABLED)

    buttons = ttk.Frame(frame)
    buttons.pack(fill=tk.X, pady=(10, 0))

    def _close(ok: bool) -> None:
        result["ok"] = ok
        win.destroy()

    ttk.Button(buttons, text="cancel", command=lambda: _close(False)).pack(side=tk.RIGHT)
    ttk.Button(buttons, text="queue removal", command=lambda: _close(True)).pack(side=tk.RIGHT, padx=(0, 6))

    win.protocol("WM_DELETE_WINDOW", lambda: _close(False))
    win.wait_window()
    return bool(result["ok"])


def _build_remove_archive_change(archive_item: dict, affected_directives: list[dict], counts: Counter, affected_text: str) -> dict:
    """Build queued remove-archive operation including affected directive summary/list."""
    archive_hash = str(archive_item.get("Hash", "") or "")
    archive_name = str(archive_item.get("Name", "") or archive_hash)
    count_summary = ", ".join(f"{counts[key]} {key}" for key in sorted(counts)) or "0 directives"
    return {
        "op": "remove-archive",
        "queue_key": f"remove-archive::{archive_hash}",
        "archive_hash": archive_hash,
        "archive_snapshot": {
            "Hash": archive_hash,
            "Name": archive_item.get("Name", ""),
        },
        "affected_directive_snapshots": [_directive_snapshot(item) for item in affected_directives],
        "affected_text": affected_text,
        "display": {
            "summary": f"REMOVE Archive: {archive_name}",
            "details": [
                f"- Remove Archives entry '{archive_name}' [{archive_hash}]",
                f"- Remove directives using this archive: {count_summary}",
            ],
            "long_text": affected_text,
        },
    }


def _apply_queued_modlist_operations(raw_modlist: bytes, queued_changes: list[dict]) -> tuple[bytes, dict]:
    """Apply queued directive edits and return (new_modlist_raw, zip_mutations)."""
    try:
        obj = json.loads(raw_modlist)
    except json.JSONDecodeError as exc:
        raise ValueError(f"modlist JSON parse failed: {exc}") from exc

    if not isinstance(obj, dict):
        raise ValueError("modlist root is not a JSON object.")

    directives = obj.get("Directives")
    if not isinstance(directives, list):
        raise ValueError("modlist Directives key is missing or not a list.")
    archives = obj.get("Archives")
    if not isinstance(archives, list):
        raise ValueError("modlist Archives key is missing or not a list.")

    replacements: dict[str, bytes] = {}
    additions: dict[str, bytes] = {}
    deletions: set[str] = set()

    for change in queued_changes:
        op = str(change.get("op", "") or "")
        if op == "replace-inline":
            idx = _find_directive_index(directives, change["directive_snapshot"])
            d = directives[idx]
            d["Hash"] = str(change["new_hash"])
            d["Size"] = int(change["new_size"])
            replacements[str(change["source_id"])] = change["new_data"]
        elif op == "delete-inline":
            idx = _find_directive_index(directives, change["directive_snapshot"])
            directives.pop(idx)
            deletions.add(str(change["source_id"]))
        elif op == "add-inline":
            directives.append(dict(change["new_directive"]))
            additions[str(change["source_id"])] = change["new_data"]
        elif op == "convert-fromarchive-to-inline":
            idx = _find_directive_index(directives, change["from_snapshot"])
            directives.pop(idx)
            directives.append(dict(change["new_directive"]))
            additions[str(change["source_id"])] = change["new_data"]
        elif op == "remove-archive":
            archive_hash = str(change.get("archive_hash", "") or "")
            archive_matches = [
                i for i, item in enumerate(archives)
                if isinstance(item, dict) and str(item.get("Hash", "") or "") == archive_hash
            ]
            if len(archive_matches) != 1:
                raise ValueError(
                    f"Could not uniquely match archive hash '{archive_hash}' in Archives (matches={len(archive_matches)})."
                )
            archives.pop(archive_matches[0])

            kept_directives: list = []
            removed_patch_ids: set[str] = set()
            for item in directives:
                if isinstance(item, dict) and _archive_hash_from_directive(item) == archive_hash:
                    patch_id = str(item.get("PatchID", "") or "")
                    if patch_id:
                        removed_patch_ids.add(patch_id)
                    continue
                kept_directives.append(item)
            directives = kept_directives

            for patch_id in removed_patch_ids:
                still_used = False
                for item in directives:
                    if isinstance(item, dict) and str(item.get("PatchID", "") or "") == patch_id:
                        still_used = True
                        break
                if not still_used:
                    deletions.add(patch_id)
        else:
            raise ValueError(f"Unsupported queued operation '{op}'.")

    obj["Directives"] = directives
    obj["Archives"] = archives
    new_raw = json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")
    return new_raw, {
        "replacements": replacements,
        "additions": additions,
        "deletions": deletions,
    }


def _apply_queued_inline_changes(
    wabba: "WabbaFile",
    queued_changes: list[dict],
    *,
    save_as_path: str | None = None,
) -> bool:
    """Apply queued InlineFile edit operations in-place or to save-as destination."""
    from ..wabba_file import WabbaFile

    if not queued_changes:
        messagebox.showinfo("InlineFile changes", "No queued changes to apply.")
        return False

    root = getattr(tk, "_default_root", None)
    if root is None:
        messagebox.showerror("InlineFile changes", "No active UI root window.")
        return False

    progress = _ReplaceInlineProgressDialog(root)
    elapsed_t0 = time.monotonic()
    progress_q: "queue.Queue[dict]" = queue.Queue()

    state = {
        "has_error": False,
        "error": None,
        "worker_done": False,
        "latest_progress": None,
        "target_path": save_as_path or wabba.path,
        "applied_count": len(queued_changes),
    }

    def _on_progress(event: dict) -> None:
        progress_q.put({"kind": "progress", "event": event})

    def _on_status(message: str) -> None:
        progress_q.put({"kind": "status", "message": message})

    def _worker() -> None:
        target_wabba: WabbaFile | None = None
        same_target = False
        try:
            _on_status("updating modlist json")
            try:
                old_modlist_raw = wabba.read_modlist()
            except Exception as exc:
                raise RuntimeError(f"Failed to read modlist:\n{exc}") from exc

            old_copy = old_modlist_raw if json_edit_byte_compare else None
            new_modlist_raw, zip_ops = _apply_queued_modlist_operations(
                old_modlist_raw,
                queued_changes,
            )

            if old_copy is not None:
                eq_start, eq_end = _matching_edge_bytes(old_copy, new_modlist_raw)
                changed = max(len(old_copy), len(new_modlist_raw)) - eq_start - eq_end
                print(
                    "modlist json updated: "
                    f"first {_human_bytes(eq_start)} equal, "
                    f"{_human_bytes(changed)} changed, "
                    f"final {_human_bytes(eq_end)} equal"
                )

            replacements: dict[str, bytes] = {"modlist": new_modlist_raw}
            replacements.update(zip_ops["replacements"])
            additions: dict[str, bytes] = dict(zip_ops["additions"])
            deletions: set[str] = set(zip_ops["deletions"])

            dest_path = save_as_path
            same_target = (
                bool(dest_path)
                and os.path.normcase(os.path.abspath(dest_path))
                == os.path.normcase(os.path.abspath(wabba.path))
            )

            if dest_path and not same_target:
                _on_status("preparing save-as archive")
                shutil.copy2(wabba.path, dest_path)
                target_wabba = WabbaFile(dest_path)
                target_wabba.open()
            else:
                target_wabba = wabba

            _on_status("preparing zip archive edit")
            target_wabba.close()
            target_wabba.set_writable_mode(True)
            target_wabba.open()
            target_wabba.rewrite_with_mutations(
                replacements=replacements,
                additions=additions,
                deletions=deletions,
                on_progress=_on_progress,
            )
        except Exception as exc:
            progress_q.put({"kind": "error", "error": exc})
        finally:
            try:
                if target_wabba is not None:
                    target_wabba.close()
                    target_wabba.set_writable_mode(False)
                    if target_wabba is wabba:
                        target_wabba.open()
            except Exception:
                pass
            if target_wabba is not None and target_wabba is not wabba:
                try:
                    target_wabba.close()
                except Exception:
                    pass
            progress_q.put({"kind": "done"})

    def _finish_ui() -> None:
        elapsed_text = _human_elapsed(time.monotonic() - elapsed_t0)
        progress.close()

        if state["has_error"]:
            summary = (
                "Apply queued InlineFile changes failed.\n\n"
                f"Changes: {state['applied_count']}\n"
                f"Target: {state['target_path']}\n"
                f"Elapsed: {elapsed_text}\n"
                f"Error: {state['error']}"
            )
            print(_flatten_summary_for_console(summary))
            messagebox.showerror("InlineFile changes", summary)
            return

        summary = (
            "Queued InlineFile changes applied successfully.\n\n"
            f"Changes: {state['applied_count']}\n"
            f"Target: {state['target_path']}\n"
            f"Elapsed: {elapsed_text}"
        )
        print(_flatten_summary_for_console(summary))
        messagebox.showinfo("InlineFile changes", summary)

    def _poll_progress() -> None:
        while True:
            try:
                item = progress_q.get_nowait()
            except queue.Empty:
                break
            kind = item.get("kind")
            if kind == "progress":
                ev = item.get("event", {})
                prev_phase = progress._last_phase
                state["latest_progress"] = ev
                if str(ev.get("phase", "")) != prev_phase:
                    progress.apply_progress_event(ev, force=True)
            elif kind == "status":
                msg = str(item.get("message", ""))
                if msg:
                    progress.set_status(msg)
            elif kind == "error":
                state["has_error"] = True
                state["error"] = item.get("error")
            elif kind == "done":
                state["worker_done"] = True

        latest = state["latest_progress"]
        if latest is not None:
            progress.apply_progress_event(latest, force=False)

        if state["worker_done"]:
            latest = state["latest_progress"]
            if latest is not None:
                progress.apply_progress_event(latest, force=True)
            _finish_ui()
            return

        progress.after(150, _poll_progress)

    threading.Thread(target=_worker, daemon=True).start()
    progress.after(150, _poll_progress)
    progress._win.wait_window()
    return not bool(state["has_error"])


def _do_replace_inline(
    wabba: "WabbaFile",
    directive: dict,
    all_directives: list,
    *,
    on_queue_upsert: Callable[[dict], None] | None = None,
    on_apply_now: Callable[[], bool] | None = None,
    on_save_as_now: Callable[[], bool] | None = None,
    on_busy_change: Callable[[bool], None] | None = None,
) -> bool:
    """Queue one InlineFile replacement, then optionally apply immediately."""
    if directive.get("$type") != "InlineFile":
        messagebox.showerror("Replace InlineFile", "Selected directive is not InlineFile.")
        return False

    source_id = directive.get("SourceDataID", "")
    to_path = directive.get("To", "")
    if not source_id or not to_path:
        messagebox.showerror(
            "Replace InlineFile",
            "Selected InlineFile is missing SourceDataID or To.",
        )
        return False

    refs = _find_source_id_references(all_directives, directive, source_id)
    if refs:
        detail = "\n".join(refs[:40])
        if len(refs) > 40:
            detail += f"\n... ({len(refs) - 40} more)"
        messagebox.showerror(
            "Replace InlineFile",
            "Abort: SourceDataID is used by other directives.\n\n" + detail,
        )
        return False

    ext = os.path.splitext(to_path.replace("\\", "/"))[1].lower()
    if ext:
        filetypes = [
            (f"{ext} files", f"*{ext}"),
            ("All files", "*.*"),
        ]
    else:
        filetypes = [("All files", "*.*")]

    picked = filedialog.askopenfilename(
        title="Replace InlineFile",
        filetypes=filetypes,
    )
    if not picked:
        return False

    print(f"[edit] replacement file selected: {picked}")

    try:
        replacement_data = _read_local_file_bytes(picked)
    except RuntimeError as exc:
        messagebox.showerror("Replace InlineFile", str(exc))
        return False

    change = _build_inline_replacement_change(directive, picked, replacement_data)
    if on_queue_upsert is not None:
        on_queue_upsert(change)

    root = getattr(tk, "_default_root", None)
    if root is None:
        messagebox.showerror("Replace InlineFile", "No active UI root window.")
        return False

    action = _prompt_replace_inline_action(root)
    if action == "queue":
        summary = (
            "InlineFile change queued for later.\n\n"
            f"To: {to_path}\n"
            f"SourceDataID: {source_id}\n"
            f"Hash: {change['new_hash']}\n"
            f"Size: {change['new_size']}"
        )
        print(_flatten_summary_for_console(summary))
        return True

    if on_busy_change is not None:
        on_busy_change(True)
    try:
        if action == "apply_now":
            if on_apply_now is None:
                return False
            return bool(on_apply_now())
        if action == "save_as_now":
            if on_save_as_now is None:
                return False
            return bool(on_save_as_now())
        return False
    finally:
        if on_busy_change is not None:
            on_busy_change(False)


def _do_delete_inline(
    directive: dict,
    all_directives: list,
    *,
    on_queue_upsert: Callable[[dict], None] | None = None,
    on_apply_now: Callable[[], bool] | None = None,
    on_save_as_now: Callable[[], bool] | None = None,
    on_busy_change: Callable[[bool], None] | None = None,
) -> bool:
    """Queue delete of one InlineFile directive + payload, optional immediate apply."""
    if directive.get("$type") != "InlineFile":
        messagebox.showerror("Delete InlineFile", "Selected directive is not InlineFile.")
        return False

    source_id = str(directive.get("SourceDataID", "") or "")
    to_path = str(directive.get("To", "") or "")
    if not source_id or not to_path:
        messagebox.showerror("Delete InlineFile", "Selected InlineFile is missing SourceDataID or To.")
        return False

    refs = _find_source_id_references(all_directives, directive, source_id)
    if refs:
        detail = "\n".join(refs[:40])
        if len(refs) > 40:
            detail += f"\n... ({len(refs) - 40} more)"
        messagebox.showerror(
            "Delete InlineFile",
            "Abort: SourceDataID is used by other directives.\n\n" + detail,
        )
        return False

    change = _build_inline_delete_change(directive)
    if on_queue_upsert is not None:
        on_queue_upsert(change)

    root = getattr(tk, "_default_root", None)
    if root is None:
        messagebox.showerror("Delete InlineFile", "No active UI root window.")
        return False

    action = _prompt_queued_action(
        root,
        title="Delete InlineFile",
        message="Delete operation was queued. What do you want to do now?",
    )
    if action == "queue":
        print(_flatten_summary_for_console(f"InlineFile delete queued: {to_path} [{source_id}]"))
        return True

    if on_busy_change is not None:
        on_busy_change(True)
    try:
        if action == "apply_now" and on_apply_now is not None:
            return bool(on_apply_now())
        if action == "save_as_now" and on_save_as_now is not None:
            return bool(on_save_as_now())
        return False
    finally:
        if on_busy_change is not None:
            on_busy_change(False)


def _do_add_inline_in_folder(
    target_folder: str,
    *,
    on_queue_upsert: Callable[[dict], None] | None = None,
    on_apply_now: Callable[[], bool] | None = None,
    on_save_as_now: Callable[[], bool] | None = None,
    on_busy_change: Callable[[bool], None] | None = None,
) -> bool:
    """Queue add of new InlineFile in target folder, optional immediate apply."""
    picked = _select_local_file(title="Add InlineFile in this folder")
    if not picked:
        return False

    try:
        data = _read_local_file_bytes(picked)
    except RuntimeError as exc:
        messagebox.showerror("Add InlineFile", str(exc))
        return False

    base = os.path.basename(picked)
    folder_norm = target_folder.replace("\\", "/").strip("/")
    to_slash = f"{folder_norm}/{base}" if folder_norm else base
    change = _build_inline_add_change(to_slash.replace("/", "\\"), picked, data)
    if on_queue_upsert is not None:
        on_queue_upsert(change)

    root = getattr(tk, "_default_root", None)
    if root is None:
        messagebox.showerror("Add InlineFile", "No active UI root window.")
        return False

    action = _prompt_queued_action(
        root,
        title="Add InlineFile",
        message="Add operation was queued. What do you want to do now?",
    )
    if action == "queue":
        print(_flatten_summary_for_console(f"InlineFile add queued: {change['to_path']} [{change['source_id']}]"))
        return True

    if on_busy_change is not None:
        on_busy_change(True)
    try:
        if action == "apply_now" and on_apply_now is not None:
            return bool(on_apply_now())
        if action == "save_as_now" and on_save_as_now is not None:
            return bool(on_save_as_now())
        return False
    finally:
        if on_busy_change is not None:
            on_busy_change(False)


def _do_add_inline_with_path(
    target_folder: str,
    *,
    on_queue_upsert: Callable[[dict], None] | None = None,
    on_apply_now: Callable[[], bool] | None = None,
    on_save_as_now: Callable[[], bool] | None = None,
    on_busy_change: Callable[[bool], None] | None = None,
) -> bool:
    """Queue add InlineFile with explicit full destination path prompt."""
    picked = _select_local_file(title="Add InlineFile with path")
    if not picked:
        return False

    try:
        data = _read_local_file_bytes(picked)
    except RuntimeError as exc:
        messagebox.showerror("Add InlineFile with path", str(exc))
        return False

    base = os.path.basename(picked)
    folder_norm = target_folder.replace("\\", "/").strip("/")
    suggested_slash = f"{folder_norm}/{base}" if folder_norm else base
    to_path = _prompt_inline_to_path(initial_to_path=suggested_slash.replace("/", "\\"))
    if to_path is None:
        return False
    if not to_path:
        messagebox.showerror("Add InlineFile with path", "Destination path cannot be empty.")
        return False

    change = _build_inline_add_change(to_path, picked, data)
    if on_queue_upsert is not None:
        on_queue_upsert(change)

    root = getattr(tk, "_default_root", None)
    if root is None:
        messagebox.showerror("Add InlineFile with path", "No active UI root window.")
        return False

    action = _prompt_queued_action(
        root,
        title="Add InlineFile with path",
        message="Add operation was queued. What do you want to do now?",
    )
    if action == "queue":
        print(_flatten_summary_for_console(f"InlineFile add queued: {change['to_path']} [{change['source_id']}]"))
        return True

    if on_busy_change is not None:
        on_busy_change(True)
    try:
        if action == "apply_now" and on_apply_now is not None:
            return bool(on_apply_now())
        if action == "save_as_now" and on_save_as_now is not None:
            return bool(on_save_as_now())
        return False
    finally:
        if on_busy_change is not None:
            on_busy_change(False)


def _do_remove_archive_and_directives(
    archive_item: dict,
    all_directives: list,
    *,
    on_queue_upsert: Callable[[dict], None] | None = None,
    on_apply_now: Callable[[], bool] | None = None,
    on_save_as_now: Callable[[], bool] | None = None,
    on_busy_change: Callable[[bool], None] | None = None,
) -> bool:
    """Queue removal of one Archives entry and all directives that reference it."""
    archive_hash = str(archive_item.get("Hash", "") or "")
    if not archive_hash:
        messagebox.showerror("Remove archive", "Selected archive entry is missing Hash.")
        return False

    affected, counts, affected_text = _collect_archive_affected_directives(archive_item, all_directives)
    root = getattr(tk, "_default_root", None)
    if root is None:
        messagebox.showerror("Remove archive", "No active UI root window.")
        return False
    if not _confirm_remove_archive_change(root, archive_item, counts, affected_text):
        return False

    change = _build_remove_archive_change(archive_item, affected, counts, affected_text)
    if on_queue_upsert is not None:
        on_queue_upsert(change)

    action = _prompt_queued_action(
        root,
        title="Remove archive and directives",
        message="Archive removal was queued. What do you want to do now?",
    )
    if action == "queue":
        print(_flatten_summary_for_console(f"Archive removal queued: {archive_item.get('Name', archive_hash)} [{archive_hash}]"))
        return True

    if on_busy_change is not None:
        on_busy_change(True)
    try:
        if action == "apply_now" and on_apply_now is not None:
            return bool(on_apply_now())
        if action == "save_as_now" and on_save_as_now is not None:
            return bool(on_save_as_now())
        return False
    finally:
        if on_busy_change is not None:
            on_busy_change(False)


def _do_convert_fromarchive_to_inline(
    directive: dict,
    *,
    on_queue_upsert: Callable[[dict], None] | None = None,
    on_apply_now: Callable[[], bool] | None = None,
    on_save_as_now: Callable[[], bool] | None = None,
    on_busy_change: Callable[[bool], None] | None = None,
) -> bool:
    """Queue conversion from FromArchive to new InlineFile, optional apply now."""
    if directive.get("$type") != "FromArchive":
        messagebox.showerror("Convert To InlineFile", "Selected directive is not FromArchive.")
        return False

    to_path = str(directive.get("To", "") or "")
    ext = os.path.splitext(to_path.replace("\\", "/"))[1].lower()
    picked = _select_local_file(
        title="Replace by new InlineFile",
        ext_hint=ext,
    )
    if not picked:
        return False

    try:
        data = _read_local_file_bytes(picked)
    except RuntimeError as exc:
        messagebox.showerror("Convert To InlineFile", str(exc))
        return False

    change = _build_convert_fromarchive_to_inline_change(directive, picked, data)
    if on_queue_upsert is not None:
        on_queue_upsert(change)

    root = getattr(tk, "_default_root", None)
    if root is None:
        messagebox.showerror("Convert To InlineFile", "No active UI root window.")
        return False

    action = _prompt_queued_action(
        root,
        title="Replace by new InlineFile",
        message="Conversion was queued. What do you want to do now?",
    )
    if action == "queue":
        print(_flatten_summary_for_console(f"FromArchive->InlineFile queued: {change['to_path']} [{change['source_id']}]"))
        return True

    if on_busy_change is not None:
        on_busy_change(True)
    try:
        if action == "apply_now" and on_apply_now is not None:
            return bool(on_apply_now())
        if action == "save_as_now" and on_save_as_now is not None:
            return bool(on_save_as_now())
        return False
    finally:
        if on_busy_change is not None:
            on_busy_change(False)