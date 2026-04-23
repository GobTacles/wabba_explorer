"""Main application window and ``run_gui`` entry point (tkinter)."""

import tkinter as tk
from tkinter import ttk

from .. import __version__
from ..wabba_file import WabbaFile
from .stdout_redirect import _StdoutRedirect
from .gui_menu import _MenuMixin
from .gui_about import _AboutMixin
from .gui_background import _BackgroundMixin
from .gui_tab_modlist_json import _TabModlistJson
from .gui_tab_archives import _TabArchives
from .gui_tab_files import _TabFiles
from .gui_tab_directives import _TabDirectives
from .gui_tab_problems import _TabProblems


# ---------------------------------------------------------------------------
# Main application window
# ---------------------------------------------------------------------------

class WabbaExplorerApp(
    _MenuMixin,
    _AboutMixin,
    _BackgroundMixin,
    _TabModlistJson,
    _TabArchives,
    _TabFiles,
    _TabDirectives,
    _TabProblems,
    tk.Tk,
):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()
        self.title(f"Wabba Explorer {__version__}")
        self.geometry("1100x750")
        self.minsize(700, 500)

        self._wabba: WabbaFile | None = None
        self._modlist_data: dict | None = None
        self._modlist_keys: list[str] = []
        self._orig_stdout = __import__("sys").stdout
        self._orig_stderr = __import__("sys").stderr
        self._outer_paned: ttk.PanedWindow | None = None
        self._dir_extract_btn = None  # Extract button in Directives tab tools area

        # Monotonically increasing counter incremented each time a new file is
        # opened.  Background workers carry the load_id they were started with
        # and bail out when it no longer matches self._load_id.
        self._load_id: int = 0

        # Records when each tab was last opened (for load-time ms reporting).
        self._tab_open_times: dict[str, float] = {}

        self._recent_files: list[str] = []
        self._max_recent_files = 8
        self._recent_files_menu: tk.Menu | None = None

        self._load_recent_files()
        self._build_ui()
        self._build_menubar()
        self._redirect_output_streams()
        self.after(100, self._init_sash)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._status_var = tk.StringVar(value="Ready – open a .wabbajack file.")
        ttk.Label(
            self, textvariable=self._status_var, anchor=tk.W, relief=tk.SUNKEN
        ).pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=2)

        outer = ttk.PanedWindow(self, orient=tk.VERTICAL)
        outer.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self._outer_paned = outer

        self._main_nb = ttk.Notebook(outer)
        outer.add(self._main_nb, weight=4)

        self._build_tab_modlist_json()
        self._build_tab_file_explorer()
        self._build_tab_archives()
        self._build_tab_directives()
        self._build_tab_problems()
        self._main_nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        bottom_nb = ttk.Notebook(outer)
        outer.add(bottom_nb, weight=1)

        console_frame = ttk.Frame(bottom_nb)
        bottom_nb.add(console_frame, text="Console")

        self._console_text = tk.Text(
            console_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 9),
            background="#1e1e1e",
            foreground="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        console_scroll = ttk.Scrollbar(console_frame, command=self._console_text.yview)
        self._console_text.configure(yscrollcommand=console_scroll.set)
        console_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._console_text.pack(fill=tk.BOTH, expand=True)
        self._console_text.bind("<Control-c>", self._console_copy)
        self._console_text.bind("<Button-3>", self._console_right_click)

    def _redirect_output_streams(self) -> None:
        import sys
        sys.stdout = _StdoutRedirect(self._console_text, self._orig_stdout)
        sys.stderr = _StdoutRedirect(self._console_text, self._orig_stderr)

    # ------------------------------------------------------------------
    # Sash initialisation
    # ------------------------------------------------------------------

    def _init_sash(self) -> None:
        """Place the sash so the console takes ~20 % of the paned area."""
        if self._outer_paned is None:
            return
        total = self._outer_paned.winfo_height()
        if total > 1:
            self._outer_paned.sashpos(0, int(total * 0.80))

    # ------------------------------------------------------------------
    # Console helpers
    # ------------------------------------------------------------------

    def _console_copy(self, _event=None) -> str:
        try:
            selected = self._console_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            self.clipboard_clear()
            self.clipboard_append(selected)
        except tk.TclError:
            pass
        return "break"

    def _console_right_click(self, event) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Copy selection", command=self._console_copy)
        menu.add_command(label="Select all", command=self._console_select_all)
        menu.tk_popup(event.x_root, event.y_root)

    def _console_select_all(self) -> None:
        self._console_text.tag_add(tk.SEL, "1.0", tk.END)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def destroy(self) -> None:
        import sys
        if isinstance(sys.stdout, _StdoutRedirect):
            sys.stdout = self._orig_stdout
        if isinstance(sys.stderr, _StdoutRedirect):
            sys.stderr = self._orig_stderr
        if self._wabba is not None:
            if self._wabba.cache is not None:
                self._wabba.cache.cancelled = True
            self._wabba.close()
        super().destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_gui(initial_file: str | None = None, *, auto_open_recent: bool = False) -> None:
    """Launch the GUI.  Optionally open *initial_file* on start-up.

    When *auto_open_recent* is True and no *initial_file* is given, the most
    recent file from the persistent recent-files list is opened automatically.
    """
    print(f"wabba_explorer {__version__}")
    app = WabbaExplorerApp()
    if initial_file:
        app._load_file(initial_file)
    elif auto_open_recent and app._recent_files:
        app._load_file(app._recent_files[0])
    app.mainloop()

