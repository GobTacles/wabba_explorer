"""Background-task methods for WabbaExplorerApp.

This module re-exports ``_BackgroundMixin`` as a thin composition of three
focused sub-mixins:

* :mod:`.gui_background_loader`  – file open/load and the background pipeline
* :mod:`.gui_background_tabs`    – tab-change handling, polling, filter sync
* :mod:`.gui_background_diff`    – problems worker, D:Archives, D:Directives

Import ``_BackgroundMixin`` from here as before; the split is transparent to
``app.py`` and all other callers.
"""

from .gui_background_loader import _BackgroundLoaderMixin
from .gui_background_tabs import _BackgroundTabsMixin
from .gui_background_diff import _BackgroundDiffMixin


class _BackgroundMixin(_BackgroundLoaderMixin, _BackgroundTabsMixin, _BackgroundDiffMixin):
    """Composed background-task mixin for WabbaExplorerApp."""
