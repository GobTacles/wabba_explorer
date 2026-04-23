"""Cross-file diff helpers for compare mode.

The main entry point is :func:`diff_directives`, which compares the
``Directives`` lists from two open ``WabbaCache`` objects and returns
the items that differ.

Key challenge – UUID normalization:
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
