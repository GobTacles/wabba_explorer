"""WabbaFile – modular loader for .wabbajack archives.

A .wabbajack file is a ZIP archive.  This class keeps the ZipFile handle
open so large archives are never fully extracted to disk.  Create one
instance per file; you can have several open simultaneously for side-by-side
comparison.
"""

import zipfile
import json
import os
import struct
import tempfile
import time
import zlib
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .wabba.cache import WabbaCache


class WabbaFile:
    """Represents one open .wabbajack archive."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._open_mode: str = "r"
        #: Cache populated by background workers after the file is opened.
        #: Created by ``WabbaExplorerApp._load_file`` and attached here so
        #: panels can reach all pre-computed data through a single object.
        self.cache: "WabbaCache | None" = None

    # ------------------------------------------------------------------
    # Context-manager support
    # ------------------------------------------------------------------

    def open(self) -> "WabbaFile":
        """Open the archive and return *self* for chaining."""
        self._zip = zipfile.ZipFile(self.path, self._open_mode)
        return self

    def set_writable_mode(self, writable: bool = True) -> None:
        """Switch default open mode between read-only and writable.

        Must be called while the archive is closed.
        """
        if self._zip is not None:
            raise RuntimeError("Cannot change mode while archive is open.")
        self._open_mode = "a" if writable else "r"

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

    @staticmethod
    def _clone_zipinfo(zi: "zipfile.ZipInfo") -> "zipfile.ZipInfo":
        """Return a writable ZipInfo clone preserving key metadata."""
        clone = zipfile.ZipInfo(zi.filename, zi.date_time)
        clone.compress_type = zi.compress_type
        clone.comment = zi.comment
        clone.extra = zi.extra
        clone.internal_attr = zi.internal_attr
        clone.external_attr = zi.external_attr
        clone.create_system = zi.create_system
        clone.flag_bits = zi.flag_bits
        return clone

    @staticmethod
    def _encode_name(name: str, flag_bits: int) -> bytes:
        """Encode ZIP entry name according to UTF-8 flag semantics."""
        if flag_bits & 0x800:
            return name.encode("utf-8")
        return name.encode("cp437", errors="replace")

    @staticmethod
    def _dos_date_time(dt: tuple[int, int, int, int, int, int]) -> tuple[int, int]:
        """Convert date-time tuple to DOS date/time fields."""
        year, month, day, hour, minute, sec = dt
        if year < 1980:
            year, month, day, hour, minute, sec = 1980, 1, 1, 0, 0, 0
        dostime = (hour << 11) | (minute << 5) | (sec // 2)
        dosdate = ((year - 1980) << 9) | (month << 5) | day
        return dostime, dosdate

    @staticmethod
    def _read_local_header_info(fp, offset: int) -> tuple[int, int, int]:
        """Read local-header variable lengths and return (name_len, extra_len, flags)."""
        fp.seek(offset)
        fixed = fp.read(30)
        if len(fixed) != 30:
            raise RuntimeError(f"Truncated local header at offset {offset}")
        sig, _ver, flags, _comp, _tm, _dt, _crc, _csz, _usz, nlen, xlen = struct.unpack(
            "<IHHHHHIIIHH", fixed
        )
        if sig != 0x04034B50:
            raise RuntimeError(
                f"Invalid local header signature at {offset}: {sig:#x}"
            )
        return nlen, xlen, flags

    @staticmethod
    def _descriptor_length(fp, data_end: int, zi: "zipfile.ZipInfo") -> int:
        """Return trailing data-descriptor length (0/12/16) for local record."""
        if not (zi.flag_bits & 0x08):
            return 0
        fp.seek(data_end)
        probe = fp.read(16)
        if len(probe) < 12:
            raise RuntimeError(f"Truncated data descriptor at {data_end}")

        def _u32(buf: bytes, off: int) -> int:
            return struct.unpack_from("<I", buf, off)[0]

        exp_crc = zi.CRC & 0xFFFFFFFF
        exp_csz = zi.compress_size & 0xFFFFFFFF
        exp_usz = zi.file_size & 0xFFFFFFFF

        # Signature-less descriptor.
        crc0, csz0, usz0 = _u32(probe, 0), _u32(probe, 4), _u32(probe, 8)
        if crc0 == exp_crc and csz0 == exp_csz and usz0 == exp_usz:
            return 12

        # Descriptor with signature 0x08074b50.
        sig = _u32(probe, 0)
        if sig == 0x08074B50:
            if len(probe) < 16:
                raise RuntimeError(f"Truncated signed data descriptor at {data_end}")
            crc1, csz1, usz1 = _u32(probe, 4), _u32(probe, 8), _u32(probe, 12)
            if crc1 == exp_crc and csz1 == exp_csz and usz1 == exp_usz:
                return 16

        raise RuntimeError(
            "Unsupported or unrecognized data descriptor layout for "
            f"'{zi.filename}' at offset {data_end}"
        )

    @staticmethod
    def _copy_range(src, dst, start: int, length: int, chunk_size: int) -> int:
        """Copy *length* bytes from src[start:] to dst and return bytes copied."""
        src.seek(start)
        remaining = length
        copied = 0
        while remaining > 0:
            block = src.read(min(chunk_size, remaining))
            if not block:
                raise RuntimeError(
                    f"Unexpected EOF while copying range at {start} (remaining {remaining})"
                )
            dst.write(block)
            n = len(block)
            copied += n
            remaining -= n
        return copied

    @staticmethod
    def _compress_replacement(
        zi: "zipfile.ZipInfo",
        raw_data: bytes,
    ) -> tuple[bytes, int, int, int]:
        """Return (compressed, crc32, uncompressed_size, compressed_size)."""
        crc = zlib.crc32(raw_data) & 0xFFFFFFFF
        if zi.compress_type == zipfile.ZIP_STORED:
            comp = raw_data
        elif zi.compress_type == zipfile.ZIP_DEFLATED:
            co = zlib.compressobj(level=zlib.Z_DEFAULT_COMPRESSION, wbits=-15)
            comp = co.compress(raw_data) + co.flush()
        else:
            raise RuntimeError(
                "Unsupported compression type for replacement entry "
                f"'{zi.filename}': {zi.compress_type}"
            )
        return comp, crc, len(raw_data), len(comp)

    @staticmethod
    def _write_local_header(
        fp,
        *,
        zi: "zipfile.ZipInfo",
        name_bytes: bytes,
        extra_bytes: bytes,
        crc: int,
        compress_size: int,
        file_size: int,
        flag_bits: int,
    ) -> None:
        """Write one local-file-header record."""
        dostime, dosdate = WabbaFile._dos_date_time(zi.date_time)
        header = struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            zi.extract_version,
            flag_bits,
            zi.compress_type,
            dostime,
            dosdate,
            crc,
            compress_size,
            file_size,
            len(name_bytes),
            len(extra_bytes),
        )
        fp.write(header)
        fp.write(name_bytes)
        fp.write(extra_bytes)

    @staticmethod
    def _write_central_dir_and_eocd(fp, records: list[dict], start_cdir: int) -> int:
        """Write central directory records and EOCD footer.

        Returns total bytes written for central directory + EOCD.
        """
        cdir_start = start_cdir
        for rec in records:
            zi = rec["zi"]
            name_bytes = rec["name_bytes"]
            extra_bytes = rec["extra_bytes"]
            comment_bytes = rec["comment_bytes"]
            dostime, dosdate = WabbaFile._dos_date_time(zi.date_time)

            if rec["header_offset"] > 0xFFFFFFFF:
                raise RuntimeError("ZIP64 not supported: header offset exceeds 4 GiB")
            if rec["compress_size"] > 0xFFFFFFFF or rec["file_size"] > 0xFFFFFFFF:
                raise RuntimeError("ZIP64 not supported: entry size exceeds 4 GiB")

            cent = struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50,
                zi.create_version,
                zi.extract_version,
                rec["flag_bits"],
                zi.compress_type,
                dostime,
                dosdate,
                rec["crc"],
                rec["compress_size"],
                rec["file_size"],
                len(name_bytes),
                len(extra_bytes),
                len(comment_bytes),
                0,
                0,
                zi.external_attr,
                rec["header_offset"],
            )
            fp.write(cent)
            fp.write(name_bytes)
            fp.write(extra_bytes)
            fp.write(comment_bytes)

        cdir_end = fp.tell()
        cdir_size = cdir_end - cdir_start
        entries = len(records)

        if entries > 0xFFFF:
            raise RuntimeError("ZIP64 not supported: entry count exceeds 65535")
        if cdir_start > 0xFFFFFFFF or cdir_size > 0xFFFFFFFF:
            raise RuntimeError("ZIP64 not supported: central directory exceeds 4 GiB")

        eocd = struct.pack(
            "<IHHHHIIH",
            0x06054B50,
            0,
            0,
            entries,
            entries,
            cdir_size,
            cdir_start,
            0,
        )
        fp.write(eocd)
        return (cdir_end - cdir_start) + len(eocd)

    @staticmethod
    def _emit_progress(
        on_progress: "Callable[[dict], None] | None",
        *,
        phase: str,
        done: int,
        total: int,
        message: str,
    ) -> None:
        """Send progress updates to caller when callback is provided."""
        if on_progress is None:
            return
        on_progress(
            {
                "phase": phase,
                "bytes_done": done,
                "bytes_total": total,
                "message": message,
            }
        )

    def rewrite_with_replacements(
        self,
        replacements: dict[str, bytes],
        *,
        on_progress: "Callable[[dict], None] | None" = None,
        chunk_size: int = 4 * 1024 * 1024,
        log_interval_secs: float = 5.0,
    ) -> None:
        """Compatibility wrapper over :meth:`rewrite_with_mutations`."""
        self.rewrite_with_mutations(
            replacements=replacements,
            additions=None,
            deletions=None,
            on_progress=on_progress,
            chunk_size=chunk_size,
            log_interval_secs=log_interval_secs,
        )

    def rewrite_with_mutations(
        self,
        *,
        replacements: dict[str, bytes] | None = None,
        additions: dict[str, bytes] | None = None,
        deletions: set[str] | None = None,
        on_progress: "Callable[[dict], None] | None" = None,
        chunk_size: int = 4 * 1024 * 1024,
        log_interval_secs: float = 5.0,
    ) -> None:
        """Rewrite archive to replace entries by name with new byte content.

        A temporary archive is written first, then atomically swapped into place.
        This avoids duplicate-name ZIP entries and keeps unrelated entries intact.
        """
        replacements = dict(replacements or {})
        additions = dict(additions or {})
        deletions = set(deletions or set())
        if not replacements and not additions and not deletions:
            return

        zf = self._require_open()
        info_list = zf.infolist()
        existing_names = {zi.filename for zi in info_list}

        for name in replacements:
            try:
                zf.getinfo(name)
            except KeyError:
                raise FileNotFoundError(
                    f"'{name}' not found inside '{self.path}'"
                ) from None
        for name in deletions:
            try:
                zf.getinfo(name)
            except KeyError:
                raise FileNotFoundError(
                    f"'{name}' not found inside '{self.path}'"
                ) from None
        for name in additions:
            if name in existing_names and name not in deletions:
                raise RuntimeError(
                    f"Cannot add '{name}' because it already exists in archive."
                )
            if name in replacements:
                raise RuntimeError(
                    f"Entry '{name}' cannot be both replacement and addition."
                )

        # Pre-compress replacement entries so rewrite accounting uses on-disk bytes.
        payloads: dict[str, dict] = {}
        payload_local_sizes: dict[str, int] = {}
        for name, raw_data in replacements.items():
            zi_old = zf.getinfo(name)
            if zi_old.flag_bits & 0x1:
                raise RuntimeError(
                    f"Encrypted replacement entry unsupported: '{name}'"
                )
            comp, crc, usz, csz = self._compress_replacement(zi_old, raw_data)
            flag_bits = zi_old.flag_bits & ~0x08  # no data descriptor for rewritten entry
            name_bytes = self._encode_name(zi_old.filename, flag_bits)
            extra_bytes = b""
            payloads[name] = {
                "zi": zi_old,
                "comp": comp,
                "crc": crc,
                "file_size": usz,
                "compress_size": csz,
                "flag_bits": flag_bits,
                "name_bytes": name_bytes,
                "extra_bytes": extra_bytes,
                "comment_bytes": zi_old.comment,
            }
            payload_local_sizes[name] = 30 + len(name_bytes) + len(extra_bytes) + csz

        for name, raw_data in additions.items():
            zi_add = zipfile.ZipInfo(name)
            zi_add.compress_type = zipfile.ZIP_DEFLATED
            zi_add.flag_bits = 0
            zi_add.comment = b""
            zi_add.extra = b""
            comp, crc, usz, csz = self._compress_replacement(zi_add, raw_data)
            flag_bits = 0
            name_bytes = self._encode_name(name, flag_bits)
            extra_bytes = b""
            payloads[name] = {
                "zi": zi_add,
                "comp": comp,
                "crc": crc,
                "file_size": usz,
                "compress_size": csz,
                "flag_bits": flag_bits,
                "name_bytes": name_bytes,
                "extra_bytes": extra_bytes,
                "comment_bytes": b"",
            }
            payload_local_sizes[name] = 30 + len(name_bytes) + len(extra_bytes) + csz

        kept_info_list = [
            zi for zi in info_list
            if zi.filename not in deletions and zi.filename not in replacements
        ]

        # Progress for copy/write/finalize is based on physical bytes rewritten.
        unchanged_span_total = 0
        for zi in kept_info_list:
            nlen, xlen, _ = self._read_local_header_info(zf.fp, zi.header_offset)
            data_start = zi.header_offset + 30 + nlen + xlen
            data_end = data_start + zi.compress_size
            descriptor_len = self._descriptor_length(zf.fp, data_end, zi)
            unchanged_span_total += (data_end + descriptor_len) - zi.header_offset

        kept_central = sum(
            46 + len(self._encode_name(zi.filename, zi.flag_bits)) + len(zi.extra) + len(zi.comment)
            for zi in kept_info_list
        )
        payload_central = sum(
            46 + len(payload["name_bytes"]) + len(payload["extra_bytes"]) + len(payload["comment_bytes"])
            for payload in payloads.values()
        )
        central_and_eocd_total = kept_central + payload_central + 22
        total_for_progress = unchanged_span_total + sum(payload_local_sizes.values()) + central_and_eocd_total
        done = 0

        self._emit_progress(
            on_progress,
            phase="plan",
            done=done,
            total=total_for_progress,
            message="Planning archive rewrite",
        )

        tmp_fd = None
        tmp_path = ""
        last_log = time.monotonic()

        def _log_if_due(phase: str) -> None:
            nonlocal last_log
            now = time.monotonic()
            if now - last_log < log_interval_secs:
                return
            last_log = now
            pct = (100.0 * done / total_for_progress) if total_for_progress else 100.0
            print(
                "[edit] zip rewrite: "
                f"phase={phase} "
                f"{done:,}/{total_for_progress:,} bytes ({pct:.1f}%)"
            )

        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix="wabba_explorer_edit_",
                suffix=".wabbajack.tmp",
                dir=os.path.dirname(self.path) or None,
            )
            os.close(tmp_fd)
            tmp_fd = None

            records: list[dict] = []
            with open(self.path, "rb") as src_fp, open(tmp_path, "wb") as dst_fp:
                self._emit_progress(
                    on_progress,
                    phase="copy",
                    done=done,
                    total=total_for_progress,
                    message="Copying unchanged archive entries",
                )

                for zi in info_list:
                    if zi.filename in deletions:
                        continue
                    out_off = dst_fp.tell()

                    if zi.filename in replacements:
                        payload = payloads[zi.filename]
                        self._emit_progress(
                            on_progress,
                            phase="write",
                            done=done,
                            total=total_for_progress,
                            message=f"Writing replacement ({zi.filename})",
                        )
                        self._write_local_header(
                            dst_fp,
                            zi=zi,
                            name_bytes=payload["name_bytes"],
                            extra_bytes=payload["extra_bytes"],
                            crc=payload["crc"],
                            compress_size=payload["compress_size"],
                            file_size=payload["file_size"],
                            flag_bits=payload["flag_bits"],
                        )
                        mv = memoryview(payload["comp"])
                        pos = 0
                        while pos < len(mv):
                            end = min(pos + chunk_size, len(mv))
                            dst_fp.write(mv[pos:end])
                            wrote = end - pos
                            pos = end
                            done += wrote
                            self._emit_progress(
                                on_progress,
                                phase="write",
                                done=done,
                                total=total_for_progress,
                                message=f"Writing replacement ({zi.filename})",
                            )
                            _log_if_due("write")

                        records.append(
                            {
                                "zi": zi,
                                "name_bytes": payload["name_bytes"],
                                "extra_bytes": payload["extra_bytes"],
                                "comment_bytes": payload["comment_bytes"],
                                "crc": payload["crc"],
                                "compress_size": payload["compress_size"],
                                "file_size": payload["file_size"],
                                "flag_bits": payload["flag_bits"],
                                "header_offset": out_off,
                            }
                        )
                        continue

                    nlen, xlen, _flags = self._read_local_header_info(src_fp, zi.header_offset)
                    data_start = zi.header_offset + 30 + nlen + xlen
                    data_end = data_start + zi.compress_size
                    descriptor_len = self._descriptor_length(src_fp, data_end, zi)
                    rec_len = (data_end + descriptor_len) - zi.header_offset

                    copied = self._copy_range(
                        src_fp,
                        dst_fp,
                        zi.header_offset,
                        rec_len,
                        chunk_size,
                    )
                    done += copied
                    self._emit_progress(
                        on_progress,
                        phase="copy",
                        done=done,
                        total=total_for_progress,
                        message=f"Copying unchanged entries ({zi.filename})",
                    )
                    _log_if_due("copy")

                    records.append(
                        {
                            "zi": zi,
                            "name_bytes": self._encode_name(zi.filename, zi.flag_bits),
                            "extra_bytes": zi.extra,
                            "comment_bytes": zi.comment,
                            "crc": zi.CRC,
                            "compress_size": zi.compress_size,
                            "file_size": zi.file_size,
                            "flag_bits": zi.flag_bits,
                            "header_offset": out_off,
                        }
                    )

                for name, payload in payloads.items():
                    if name in replacements:
                        # Replacements already written in-place of their original records.
                        continue

                    out_off = dst_fp.tell()
                    zi = payload["zi"]
                    self._emit_progress(
                        on_progress,
                        phase="write",
                        done=done,
                        total=total_for_progress,
                        message=f"Writing addition ({name})",
                    )
                    self._write_local_header(
                        dst_fp,
                        zi=zi,
                        name_bytes=payload["name_bytes"],
                        extra_bytes=payload["extra_bytes"],
                        crc=payload["crc"],
                        compress_size=payload["compress_size"],
                        file_size=payload["file_size"],
                        flag_bits=payload["flag_bits"],
                    )
                    mv = memoryview(payload["comp"])
                    pos = 0
                    while pos < len(mv):
                        end = min(pos + chunk_size, len(mv))
                        dst_fp.write(mv[pos:end])
                        wrote = end - pos
                        pos = end
                        done += wrote
                        self._emit_progress(
                            on_progress,
                            phase="write",
                            done=done,
                            total=total_for_progress,
                            message=f"Writing addition ({name})",
                        )
                        _log_if_due("write")

                    records.append(
                        {
                            "zi": zi,
                            "name_bytes": payload["name_bytes"],
                            "extra_bytes": payload["extra_bytes"],
                            "comment_bytes": payload["comment_bytes"],
                            "crc": payload["crc"],
                            "compress_size": payload["compress_size"],
                            "file_size": payload["file_size"],
                            "flag_bits": payload["flag_bits"],
                            "header_offset": out_off,
                        }
                    )

                self._emit_progress(
                    on_progress,
                    phase="finalize",
                    done=done,
                    total=total_for_progress,
                    message="Finalizing temporary archive",
                )
                _log_if_due("finalize")
                wrote_meta = self._write_central_dir_and_eocd(dst_fp, records, dst_fp.tell())
                done += wrote_meta
                self._emit_progress(
                    on_progress,
                    phase="finalize",
                    done=done,
                    total=total_for_progress,
                    message="Finalizing temporary archive",
                )
                _log_if_due("finalize")

            self._emit_progress(
                on_progress,
                phase="swap",
                done=done,
                total=total_for_progress,
                message="Swapping rewritten archive",
            )
            self.close()
            os.replace(tmp_path, self.path)
            self.open()
            self._emit_progress(
                on_progress,
                phase="done",
                done=total_for_progress,
                total=total_for_progress,
                message="Archive rewrite complete",
            )
        except Exception:
            try:
                if tmp_fd is not None:
                    os.close(tmp_fd)
            except OSError:
                pass
            if tmp_path:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise

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
