"""WabbaHash helpers for hash algorithms used by Wabbajack metadata.

Reference:
- https://wiki.wabbajack.org/technical_talk/Hashing%20Overview.html
- xxHash (GitHub): https://github.com/Cyan4973/xxHash
- Python package: pip install xxhash
"""

import base64
from typing import BinaryIO

import xxhash


def WabbaHashXX64(data: bytes) -> str:
    """Return WabbaHash XX64 as base64 from little-endian 64-bit digest bytes."""
    value = xxhash.xxh64(data).intdigest()
    return base64.b64encode(value.to_bytes(8, "little")).decode("ascii")


def WabbaHashXX64_stream(stream: BinaryIO, chunk_size: int = 1024 * 1024) -> str:
    """Return WabbaHash XX64 as base64 by hashing stream data in chunks."""
    hasher = xxhash.xxh64()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
    value = hasher.intdigest()
    return base64.b64encode(value.to_bytes(8, "little")).decode("ascii")
