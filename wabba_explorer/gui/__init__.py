"""gui – tkinter GUI package for wabba_explorer.

Re-exports ``run_gui`` so that the existing import path
``from wabba_explorer.gui import run_gui`` continues to work after
the old ``gui.py`` module was replaced by this package.
"""

from .app import run_gui

__all__ = ["run_gui"]
