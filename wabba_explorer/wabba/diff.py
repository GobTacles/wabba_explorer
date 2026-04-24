"""Cross-file diff helpers for compare mode.

Two entry points are provided:

* :func:`diff_directives` – compares the ``Directives`` lists from two
  ``WabbaCache`` objects and returns items that differ.
* :func:`diff_archives` – compares the ``Archives`` lists from two
  ``WabbaCache`` objects, classifying each as ``removed``, ``added``, or
  ``updated`` (same mod, different version).

Key challenge – UUID normalization (directives only):
  ``SourceDataID`` and ``PatchID`` fields are UUID filenames stored at the
  root of the ``.wabbajack`` zip archive.  When two modlist versions are
  compared those UUIDs are *not* expected to be identical, but the content
  they point to may be the same.  To handle this we replace each UUID with
  its ``(CRC32, uncompressed_size)`` signature (precomputed in
  ``WabbaCache.wabba_root_info``) before comparing.  Two directives that
  refer to different UUID filenames but with matching (CRC32, size) are
  treated as identical.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .cache import WabbaCache

# Fields whose values are UUID filenames in the wabba archive root that need
# to be resolved to content signatures before comparison.
_UUID_FIELDS = ("SourceDataID", "PatchID")


def _directive_fingerprint(
    d: dict,
    root_info: dict[str, tuple[int, int]],
) -> dict:
    """Return a comparable fingerprint for *d*.

    UUID fields (``SourceDataID``, ``PatchID``) are replaced with their
    ``(CRC32, uncompressed_size)`` content signatures from *root_info*.
    If a UUID is not found in *root_info* the raw UUID string is kept so
    the comparison still works (and the difference is flagged).

    All other fields are copied verbatim.
    """
    result: dict = {}
    for key, value in d.items():
        if key in _UUID_FIELDS and isinstance(value, str) and value:
            sig = root_info.get(value)
            result[key] = sig if sig is not None else value
        else:
            result[key] = value
    return result


def diff_directives(
    cache_a: "WabbaCache",
    cache_b: "WabbaCache",
) -> list[dict]:
    """Compare directives from two caches and return items that differ.

    Matching is done by the ``To`` destination path (the install target).
    For each ``To`` path the function classifies the directive pair as:

    * **A only** – present in A, absent in B.
    * **B only** – present in B, absent in A.
    * **changed** – present in both but fingerprints differ.

    Unchanged directives (same fingerprint on both sides) are omitted.

    Each returned item is a copy of the original directive dict augmented
    with two extra keys:

    * ``_diff_side`` – ``"A only"``, ``"B only"``, or ``"changed"``
    * ``_diff_wabba`` – ``"A"`` or ``"B"``  (which wabba this copy came from)

    For **changed** pairs two items are returned (first A, then B) so both
    versions can be inspected independently.

    The result is sorted by ``To`` path (case-insensitive).
    """
    root_info_a = getattr(cache_a, "wabba_root_info", {})
    root_info_b = getattr(cache_b, "wabba_root_info", {})

    # Build To → directive dicts (last directive wins for duplicate To paths).
    by_to_a: dict[str, dict] = {}
    for d in cache_a.directives:
        if isinstance(d, dict):
            to = d.get("To", "")
            if to:
                by_to_a[to] = d

    by_to_b: dict[str, dict] = {}
    for d in cache_b.directives:
        if isinstance(d, dict):
            to = d.get("To", "")
            if to:
                by_to_b[to] = d

    result: list[dict] = []
    all_tos = sorted(set(by_to_a) | set(by_to_b), key=str.lower)

    for to in all_tos:
        in_a = to in by_to_a
        in_b = to in by_to_b

        if in_a and not in_b:
            item = dict(by_to_a[to])
            item["_diff_side"] = "A only"
            item["_diff_wabba"] = "A"
            result.append(item)

        elif in_b and not in_a:
            item = dict(by_to_b[to])
            item["_diff_side"] = "B only"
            item["_diff_wabba"] = "B"
            result.append(item)

        else:
            # Present in both – compare normalised fingerprints.
            fp_a = _directive_fingerprint(by_to_a[to], root_info_a)
            fp_b = _directive_fingerprint(by_to_b[to], root_info_b)
            if fp_a != fp_b:
                item_a = dict(by_to_a[to])
                item_a["_diff_side"] = "changed"
                item_a["_diff_wabba"] = "A"
                result.append(item_a)

                item_b = dict(by_to_b[to])
                item_b["_diff_side"] = "changed"
                item_b["_diff_wabba"] = "B"
                result.append(item_b)

    return result


# ---------------------------------------------------------------------------
# Archive diff
# ---------------------------------------------------------------------------

# Pre-compiled patterns used by _mod_key().
_LL_RE = re.compile(r"https?://(?:www\.)?loverslab\.com/files/file/(\d+)")
_WJ_PREFIX = "https://authored-files.wabbajack.org/"
_WJ_FILE_RE = re.compile(r"[^_\-]+")
_WJ_VER_RE = re.compile(r"\s+\d[\d.]*(\.\w+)$")


def _mod_key(archive: dict) -> "tuple | None":
    """Return a stable identity key for *archive* that survives version changes.

    Supported sources:

    * **Nexus** – ``State.$type`` contains ``"NexusDownloader"``;
      key = ``("nexus", ModID)``.
    * **LoversLab** – ``State.Url`` matches
      ``https[s]://[www.]loverslab.com/files/file/<id>[...]``;
      key = ``("ll", id)``.
    * **WabbajackAuthored** – ``State.Url`` starts with
      ``https://authored-files.wabbajack.org/<name>[_-]...``;
      key = ``("wj", prefix_url_up_to_first_dash_or_underscore)``
      (``%20`` is normalised to space; trailing version numbers of the
      form ``" 7.30.7z"`` are stripped to ``".7z"``).

    Returns ``None`` when no identity can be determined.
    """
    state = archive.get("State")
    if not isinstance(state, dict):
        return None
    # Nexus
    if "NexusDownloader" in state.get("$type", ""):
        mid = state.get("ModID")
        if mid is not None:
            return ("nexus", mid)
    url = state.get("Url") or ""
    # LoversLab
    m = _LL_RE.match(url)
    if m:
        return ("ll", int(m.group(1)))
    # authored-files.wabbajack.org
    if url.startswith(_WJ_PREFIX):
        m = _WJ_FILE_RE.match(url[len(_WJ_PREFIX):])
        if m:
            base = m.group(0).replace("%20", " ")
            base = _WJ_VER_RE.sub(r"\1", base)
            return ("wj", _WJ_PREFIX + base)
    return None


def diff_archives(
    cache_a: "WabbaCache",
    cache_b: "WabbaCache",
) -> list[dict]:
    """Compare archives from two caches and return items that differ.

    Each archive entry is classified as one of:

    * **removed** – present in A, absent in B.
    * **added**   – present in B, absent in A.
    * **updated** – A-only and B-only archives that share the same mod
      identity key (e.g. same Nexus ModID).  Only 1-to-1 pairings are
      merged.
    * **removed-multipart** / **added-multipart** – A-only or B-only
      archives whose mod-key appears on *both* sides but with more than
      one archive on at least one side (multi-file mod, cannot be
      auto-paired).

    Each returned dict is a copy of the original archive dict augmented
    with extra keys:

    * ``_diff_side`` – ``"removed"``, ``"added"``, ``"updated"``,
      ``"removed-multipart"``, or ``"added-multipart"``
    * For ``"updated"`` items additionally: ``_diff_wabba="A"``,
      ``_ver_a``, ``_ver_b``, ``_b_archive``.

    Items are ordered: updated pairs first (sorted by mod name), then
    multipart items, then remaining A-only (removed), then B-only (added).
    """
    hashes_a = set(cache_a.archives_by_hash.keys())
    hashes_b = set(cache_b.archives_by_hash.keys())

    only_in_a = hashes_a - hashes_b
    only_in_b = hashes_b - hashes_a

    # mod key → list of archive dicts for A-only / B-only sides
    mods_a: dict[tuple, list[dict]] = {}
    for h in only_in_a:
        arch = cache_a.archives_by_hash[h]
        key = _mod_key(arch)
        if key is not None:
            mods_a.setdefault(key, []).append(arch)

    mods_b: dict[tuple, list[dict]] = {}
    for h in only_in_b:
        arch = cache_b.archives_by_hash[h]
        key = _mod_key(arch)
        if key is not None:
            mods_b.setdefault(key, []).append(arch)

    # Only merge when exactly one entry per side shares that key.
    shared_mod_ids = {
        key
        for key in set(mods_a) & set(mods_b)
        if len(mods_a[key]) == 1 and len(mods_b[key]) == 1
    }

    # Keys present on both sides but with >1 archive on at least one side
    # (multi-file / multi-part mods that cannot be auto-paired).
    multipart_keys = (set(mods_a) & set(mods_b)) - shared_mod_ids

    # Hashes absorbed into "updated" pairs (excluded from removed/added).
    updated_hashes_a = {mods_a[key][0]["Hash"] for key in shared_mod_ids}
    updated_hashes_b = {mods_b[key][0]["Hash"] for key in shared_mod_ids}

    # Hashes absorbed into multipart groups.
    multipart_hashes_a = {arch["Hash"] for key in multipart_keys for arch in mods_a[key]}
    multipart_hashes_b = {arch["Hash"] for key in multipart_keys for arch in mods_b[key]}

    diff_items: list[dict] = []

    # Add "updated" entries, sorted by mod name for readability.
    def _mod_sort_key(key: tuple) -> str:
        arch = mods_a[key][0]
        state = arch.get("State") or {}
        return (state.get("Name") or arch.get("Name", "")).lower()

    for mid in sorted(shared_mod_ids, key=_mod_sort_key):
        arch_a = mods_a[mid][0]
        arch_b = mods_b[mid][0]
        state_a = arch_a.get("State") or {}
        state_b = arch_b.get("State") or {}
        item = dict(arch_a)
        item["_diff_side"] = "updated"
        item["_diff_wabba"] = "A"
        item["_ver_a"] = state_a.get("Version", "?") or "?"
        item["_ver_b"] = state_b.get("Version", "?") or "?"
        item["_b_archive"] = dict(arch_b)
        diff_items.append(item)

    # Add multipart A-only items.
    for h in sorted(multipart_hashes_a):
        item = dict(cache_a.archives_by_hash[h])
        item["_diff_side"] = "removed-multipart"
        diff_items.append(item)

    # Add multipart B-only items.
    for h in sorted(multipart_hashes_b):
        item = dict(cache_b.archives_by_hash[h])
        item["_diff_side"] = "added-multipart"
        diff_items.append(item)

    # Add remaining A-only items (not part of an update or multipart group).
    for h in sorted(only_in_a - updated_hashes_a - multipart_hashes_a):
        item = dict(cache_a.archives_by_hash[h])
        item["_diff_side"] = "removed"
        diff_items.append(item)

    # Add remaining B-only items (not part of an update or multipart group).
    for h in sorted(only_in_b - updated_hashes_b - multipart_hashes_b):
        item = dict(cache_b.archives_by_hash[h])
        item["_diff_side"] = "added"
        diff_items.append(item)

    return diff_items
