"""CRC32C (Castagnoli) of an object, in the form Cloud Storage reports it.

Cloud Storage keeps a ``crc32c`` for every object: the base64 of the checksum's 4 big-endian
bytes. The browser computes the same value over the file before it sends it (``web/lib/crc32c.ts``)
and ``DocumentService.complete`` compares it with what storage holds. The local object store has
no such metadata, so ``crc32c_of`` computes it streaming in 1 MiB blocks.

The compiled ``google_crc32c`` (a dependency of google-cloud-storage) is used when it imports;
otherwise a small table-driven pure-Python version gives the same answer, slower.

GUARANTEE(test_crc32c_known_vectors_match_the_standard_check_value)
"""

from __future__ import annotations

import base64
from collections.abc import Callable
from typing import IO

_POLY = 0x82F63B78  # reflected Castagnoli polynomial
BLOCK = 1 << 20  # 1 MiB


def _table() -> tuple[int, ...]:
    out = []
    for n in range(256):
        c = n
        for _ in range(8):
            c = (c >> 1) ^ _POLY if c & 1 else c >> 1
        out.append(c)
    return tuple(out)


_TABLE = _table()


def update(crc: int, data: bytes) -> int:
    """Continue a checksum over more bytes (start with 0); the unsigned 32-bit value."""
    c = ~crc & 0xFFFFFFFF
    table = _TABLE
    for byte in data:
        c = table[(c ^ byte) & 0xFF] ^ (c >> 8)
    return ~c & 0xFFFFFFFF


def to_base64(crc: int) -> str:
    """The 4 big-endian bytes of a checksum as base64 (Cloud Storage's own form)."""
    return base64.b64encode(crc.to_bytes(4, "big")).decode("ascii")


def _extend() -> Callable[[int, bytes], int]:
    """The compiled extender when it is installed, else the table version."""
    try:
        import google_crc32c

        extender: Callable[[int, bytes], int] = google_crc32c.extend
        return extender
    except ImportError:  # pragma: no cover - google-cloud-storage always brings it
        return update


extend = _extend()


def crc32c_of(reader: IO[bytes]) -> str:
    """The checksum (base64) of everything ``reader`` yields from its current position."""
    crc = 0
    # LOOP(crc32c read): cap=object size / 1 MiB reads; exit=end of stream
    while block := reader.read(BLOCK):
        crc = extend(crc, block)
    return to_base64(crc)


def crc32c_of_bytes(data: bytes) -> str:
    """The checksum (base64) of a byte string."""
    return to_base64(update(0, data))
