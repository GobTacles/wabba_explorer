"""Problems-tab panel: hash-mismatch tree + progress bar (tkinter)."""

import tkinter as tk
from tkinter import ttk

from ..wabba_file import WabbaFile
from .fs_tree_panel import _FsTreePanel
from .gui_util import _directive_label, _archive_label

_PROBLEMS_IID = "__PROBLEMS__"


class _ProblemsPanel(_FsTreePanel):
    """Problems tab panel for InlineFile/RemappedInlineFile hash mismatches."""

    def __init__(self, parent, **kwargs) -> None:
        self._progress = None
        self._progress_var = tk.StringVar(value="")
        self._last_mismatch_count = -1
        self._stored_text = ""
        self.problem_report_lines: list[str] = []
        super().__init__(parent, **kwargs)

    def add_problem_report_line(self, line: str) -> None:
        """Append *line* to problem_report_lines and the right-side text widget."""
        self.problem_report_lines.append(line)
        self._stored_text = "\n".join(self.problem_report_lines)
        self._preview.configure(state=tk.NORMAL)
        self._preview.insert(tk.END, line + "\n")
        self._preview.see(tk.END)
        self._preview.configure(state=tk.DISABLED)

    def _build(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        self._tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self._tree.column("#0", width=350, stretch=True)
        vsb = ttk.Scrollbar(left, command=self._tree.yview)
        hsb = ttk.Scrollbar(left, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self._tree.pack(fill=tk.BOTH, expand=True)
        self._tree.bind("<<TreeviewSelect>>", self._on_select)

        right = ttk.Frame(paned)
        paned.add(right, weight=3)

        self._preview = tk.Text(
            right, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9)
        )
        sb2 = ttk.Scrollbar(right, command=self._preview.yview)
        self._preview.configure(yscrollcommand=sb2.set)
        sb2.pack(side=tk.RIGHT, fill=tk.Y)
        self._preview.pack(fill=tk.BOTH, expand=True)

        progress_row = ttk.Frame(right)
        progress_row.pack(fill=tk.X, pady=(4, 0))
        self._progress = ttk.Progressbar(progress_row, mode="determinate", maximum=1)
        self._progress.pack(fill=tk.X)
        ttk.Label(progress_row, textvariable=self._progress_var, anchor=tk.W).pack(
            fill=tk.X, pady=(2, 0)
        )

    def set_analyzing(self, *, header: str = "") -> None:
        self._tree.delete(*self._tree.get_children())
        self._all_directives = []
        self.problem_report_lines = []
        self._stored_text = ""
        self._tree.insert("", tk.END, iid=_PROBLEMS_IID, text="PROBLEMS")
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.configure(state=tk.DISABLED)
        if header:
            self.add_problem_report_line(header)
        if self._progress is not None:
            self._progress.configure(maximum=1, value=0)
        self._progress_var.set("analyzing...")
        self._last_mismatch_count = -1

    def load_directives(self, directives, wabba, archives=None) -> None:
        """Rebuild tree from mismatch directives, keeping PROBLEMS at the top."""
        super().load_directives(directives, wabba, archives)
        self._tree.insert("", 0, iid=_PROBLEMS_IID, text="PROBLEMS")

    def _on_select(self, _event=None) -> None:
        sel = self._tree.selection()
        if not sel:
            return
        if sel[0] == _PROBLEMS_IID:
            self._set_preview(self._stored_text or "(analysis not yet complete)")
            return
        self._show_preview(sel[0])

    def update_analysis(
        self,
        *,
        total: int,
        processed: int,
        matches: int,
        mismatches: int,
        ignores: int,
        elapsed: float,
        mismatch_directives: list[dict],
        wabba: WabbaFile | None,
        archives: list | None,
        done: bool,
        unused_archives: list | None = None,
        missing_archives: list[str] | None = None,
        missing_inline_files: list[str] | None = None,
        unused_inline_files: list[str] | None = None,
    ) -> None:
        total_safe = total if total > 0 else 1
        if self._progress is not None:
            self._progress.configure(maximum=total_safe, value=min(processed, total_safe))

        def _pct(value: int) -> float:
            return (100.0 * value / total) if total else 0.0

        progress_text = (
            f"processed {processed}/{total} | "
            f"matches {matches} ({_pct(matches):.1f}%) | "
            f"mismatches {mismatches} ({_pct(mismatches):.1f}%) | "
            f"ignored {ignores} ({_pct(ignores):.1f}%) | "
            f"elapsed {elapsed:.1f}s"
        )
        self._progress_var.set(progress_text)

        if mismatches != self._last_mismatch_count or done:
            self.load_directives(mismatch_directives, wabba, archives)
            self._last_mismatch_count = mismatches

        if not done:
            return

        self.add_problem_report_line("")
        self.add_problem_report_line("Directives:")
        self.add_problem_report_line(r"- NOTE: hash mismatch for profile\*\modlist.txt is normal")
        if mismatch_directives:
            for item in mismatch_directives:
                self.add_problem_report_line(f"- hash mismatch: {_directive_label(item)}")
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Unused Archives:")
        if unused_archives:
            for a in unused_archives:
                self.add_problem_report_line(f"- unused: {_archive_label(a)}")
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Missing Archives:")
        if missing_archives:
            for line in missing_archives:
                self.add_problem_report_line(line)
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Missing InlineFiles:")
        if missing_inline_files:
            for line in missing_inline_files:
                self.add_problem_report_line(line)
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Unused InlineFiles:")
        if unused_inline_files:
            for uid in unused_inline_files:
                self.add_problem_report_line(f"- unused: {uid}")
        else:
            self.add_problem_report_line("- None")

        self.add_problem_report_line("")
        self.add_problem_report_line("Summary:")
        self.add_problem_report_line(f"- processed total: {total}")
        self.add_problem_report_line(f"- hash matches: {matches} ({_pct(matches):.1f}%)")
        self.add_problem_report_line(f"- mismatches: {mismatches} ({_pct(mismatches):.1f}%)")
        self.add_problem_report_line(f"- ignored: {ignores} ({_pct(ignores):.1f}%)")
        self.add_problem_report_line(f"- elapsed: {elapsed:.1f}s")

    def _set_preview(self, text: str) -> None:
        self._preview.configure(state=tk.NORMAL)
        self._preview.delete("1.0", tk.END)
        self._preview.insert(tk.END, text)
        self._preview.configure(state=tk.DISABLED)
