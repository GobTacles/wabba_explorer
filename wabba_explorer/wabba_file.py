"""WabbaFile – modular loader for .wabbajack archives.

A .wabbajack file is a ZIP archive.  This class keeps the ZipFile handle
open so large archives are never fully extracted to disk.  Create one
instance per file; you can have several open simultaneously for side-by-side
comparison.
"""

import zipfile
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .wabba.cache import WabbaCache


class WabbaFile:
    """Represents one open .wabbajack archive."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        #: Cache populated by background workers after the file is opened.
        #: Created by ``WabbaExplorerApp._load_file`` and attached here so
        #: panels can reach all pre-computed data through a single object.
        self.cache: "WabbaCache | None" = None

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    def open(self) -> "WabbaFile":
        """Open the archive and return *self* for chaining."""
        self._zip = zipfile.ZipFile(self.path, "r")
        return self

    def close(self) -> None:
        """Close the archive handle."""
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> "WabbaFile":
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Queries (archive must be open)
    # ------------------------------------------------------------------

    def _require_open(self) -> zipfile.ZipFile:
        if self._zip is None:
            raise RuntimeError("Archive is not open – call open() first.")
        return self._zip

    def list_all(self) -> list[str]:
        """Return the full list of names inside the archive."""
        return self._require_open().namelist()

    def list_root_files(self) -> list[str]:
        """Return only entries that live directly in the root (no sub-dir)."""
        return [
            name
            for name in self._require_open().namelist()
            if "/" not in name.rstrip("/")
        ]

    def read_bytes(self, name: str) -> bytes:
        """Read *name* from the archive without extracting it to disk."""
        try:
            return self._require_open().read(name)
        except KeyError:
            raise FileNotFoundError(
                f"'{name}' not found inside '{self.path}'"
            ) from None

    def read_modlist(self) -> bytes:
        """Read the 'modlist' entry from the archive root."""
        return self.read_bytes("modlist")

    def open_member(self, name: str):
        """Open *name* in the archive for streamed reads."""
        try:
            return self._require_open().open(name, "r")
        except KeyError:
            raise FileNotFoundError(
                f"'{name}' not found inside '{self.path}'"
            ) from None

    def get_zip_info(self, name: str) -> "zipfile.ZipInfo":
        """Return the ZipInfo metadata for *name* without reading the data."""
        try:
            return self._require_open().getinfo(name)
        except KeyError:
            raise FileNotFoundError(
                f"'{name}' not found inside '{self.path}'"
            ) from None

    def read_modlist_json(self) -> dict:
        """Read 'modlist' and parse it as JSON."""
        raw = self.read_modlist()
        return json.loads(raw)

    # ------------------------------------------------------------------
    # Virtual filesystem cache built from Directive 'To' paths
    # ------------------------------------------------------------------

    @staticmethod
    def build_fs_cache(directives: list) -> dict:
        """Build a virtual filesystem cache from Directive ``To`` paths.

        Returns a ``dict`` mapping each normalised installation path
        (forward-slash separated) to its corresponding Directive entry.
        Entries whose ``To`` field is absent or empty are skipped.
        The dict is ordered by path (ascending, case-insensitive).
        """
        raw: dict[str, dict] = {}
        for directive in directives:
            if not isinstance(directive, dict):
                continue
            to = directive.get("To", "")
            if not to:
                continue
            normalised = to.replace("\\", "/")
            raw[normalised] = directive

        return dict(sorted(raw.items(), key=lambda kv: kv[0].lower()))
