"""About / Licenses dialog for WabbaExplorerApp."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
import webbrowser

from .. import __version__


_XXHASH_INFO = "xxHash — Extremely fast hash algorithm\nCopyright (C) 2012-2023 Yann Collet"

_XXHASH_LICENSE = """\
BSD 2-Clause License

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

* Redistributions of source code must retain the above copyright
  notice, this list of conditions and the following disclaimer.

* Redistributions in binary form must reproduce the above copyright
  notice, this list of conditions and the following disclaimer in
  the documentation and/or other materials provided with the
  distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
POSSIBILITY OF SUCH DAMAGE.
"""

# Each entry: (label, list of (text, url_or_None) segments, plain text body)
def _build_entries() -> list[tuple[str, list[tuple[str, str | None]], str]]:
    wabba_links = [
        ("GitHub: ", None),
        ("https://github.com/GobTacles/wabba_explorer", "https://github.com/GobTacles/wabba_explorer"),
        (f"\nVersion: {__version__}\n\nWabbajack: ", None),
        ("https://www.wabbajack.org/", "https://www.wabbajack.org/"),
    ]
    xxhash_links = [
        (_XXHASH_INFO + "\n\n", None),
        ("https://github.com/ifduyue/python-xxhash", "https://github.com/ifduyue/python-xxhash"),
        ("\n", None),
        ("https://github.com/Cyan4973/xxHash", "https://github.com/Cyan4973/xxHash"),
        ("\nLicense: ", None),
        ("https://opensource.org/licenses/BSD-2-Clause", "https://opensource.org/licenses/BSD-2-Clause"),
        ("\n\n" + _XXHASH_LICENSE, None),
    ]
    return [
        ("wabba_explorer", wabba_links, ""),
        ("xxhash", xxhash_links, ""),
    ]


class _AboutMixin:
    """Mixin that provides the About / Licenses dialog."""

    def _show_about(self) -> None:
        entries = _build_entries()

        win = tk.Toplevel(self)
        win.title("About – Wabba Explorer")
        win.resizable(True, True)
        win.geometry("720x460")
        win.grab_set()

        # ── layout: left listbox | right detail text ──────────────────
        paned = ttk.PanedWindow(win, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        left = ttk.Frame(paned)
        paned.add(left, weight=1)

        lb = tk.Listbox(left, activestyle="dotbox", exportselection=False, width=18)
        lb.pack(fill=tk.BOTH, expand=True)
        for label, _links, _body in entries:
            lb.insert(tk.END, label)

        right = ttk.Frame(paned)
        paned.add(right, weight=4)

        detail = tk.Text(
            right, wrap=tk.WORD, font=("Consolas", 9), padx=8, pady=8,
            state=tk.DISABLED, cursor="arrow",
        )
        sb = ttk.Scrollbar(right, command=detail.yview)
        detail.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        detail.pack(fill=tk.BOTH, expand=True)

        # configure a clickable hyperlink tag
        detail.tag_configure("link", foreground="#0563C1", underline=True)
        detail.tag_configure("link_hover", foreground="#0563C1", underline=True)
        detail.config(cursor="arrow")

        _link_urls: dict[str, str] = {}  # tag_name -> url

        def _open_url(url: str) -> None:
            webbrowser.open(url)

        def _populate_detail(idx: int) -> None:
            detail.configure(state=tk.NORMAL)
            detail.delete("1.0", tk.END)
            # remove old link tag bindings
            for tag in list(_link_urls):
                detail.tag_delete(tag)
            _link_urls.clear()

            _label, links, _body = entries[idx]
            link_counter = [0]
            for text_seg, url in links:
                if url:
                    tag = f"link_{link_counter[0]}"
                    link_counter[0] += 1
                    _link_urls[tag] = url
                    detail.insert(tk.END, text_seg, (tag, "link"))
                    detail.tag_bind(tag, "<Button-1>", lambda _e, u=url: _open_url(u))
                    detail.tag_bind(tag, "<Enter>", lambda _e, t=tag: detail.configure(cursor="hand2"))
                    detail.tag_bind(tag, "<Leave>", lambda _e: detail.configure(cursor="arrow"))
                else:
                    detail.insert(tk.END, text_seg)
            detail.configure(state=tk.DISABLED)

        def _on_select(_event=None) -> None:
            sel = lb.curselection()
            if sel:
                _populate_detail(sel[0])

        lb.bind("<<ListboxSelect>>", _on_select)

        # Select first entry by default
        lb.selection_set(0)
        _populate_detail(0)

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=6)
        win.bind("<Escape>", lambda _e: win.destroy())
