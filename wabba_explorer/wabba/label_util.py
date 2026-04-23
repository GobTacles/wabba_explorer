"""Pure functions that produce display-label strings from modlist data dicts.

These live here (not in ``gui/``) so background loader threads can use them
without importing any GUI code.
"""


def archive_label(item: dict) -> str:
    """Label for an Archives entry: ``'Name [Hash]'``."""
    if not isinstance(item, dict):
        return str(item)
    return f"{item.get('Name', '?')} [{item.get('Hash', '?')}]"


def directive_label(item: dict) -> str:
    """Label for a Directives entry: ``'To [Hash]'``."""
    if not isinstance(item, dict):
        return str(item)
    return f"{item.get('To', '?')} [{item.get('Hash', '?')}]"
