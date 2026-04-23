"""Pure functions that produce display-label strings from modlist data dicts.

These live here (not in ``gui/``) so background loader threads can use them
without importing any GUI code.
"""


def archive_label(item: dict) -> str:
    """Label for an Archives entry.

    For NexusDownloader entries the ``State.Name`` and ``State.Version`` are
    used in preference to the root ``Name``, giving a cleaner human-readable
    label.  Falls back to ``root-Name [Hash]`` for all other downloader types.
    """
    if not isinstance(item, dict):
        return str(item)
    state = item.get("State")
    if isinstance(state, dict) and "NexusDownloader" in state.get("$type", ""):
        name = state.get("Name") or item.get("Name", "?")
        version = state.get("Version", "")
        suffix = f" v{version}" if version else ""
        return f"{name}{suffix} [{item.get('Hash', '?')}]"
    return f"{item.get('Name', '?')} [{item.get('Hash', '?')}]"


def directive_label(item: dict) -> str:
    """Label for a Directives entry: ``'To [Hash]'``."""
    if not isinstance(item, dict):
        return str(item)
    return f"{item.get('To', '?')} [{item.get('Hash', '?')}]"
