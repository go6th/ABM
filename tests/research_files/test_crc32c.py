"""CRC32C in the form Cloud Storage reports it (base64 of the 4 big-endian bytes)."""

from __future__ import annotations

import io

from research_files import crc32c
from research_files.crc32c import crc32c_of, crc32c_of_bytes, update


def test_crc32c_known_vectors_match_the_standard_check_value() -> None:
    # the CRC-32C check value of "123456789" is 0xE3069283
    assert update(0, b"123456789") == 0xE3069283
    assert crc32c_of_bytes(b"123456789") == "4waSgw=="
    assert crc32c_of_bytes(b"") == "AAAAAA=="
    # 32 zero bytes (RFC 3720 test vector): 0x8A9136AA
    assert update(0, bytes(32)) == 0x8A9136AA
    assert crc32c_of_bytes(bytes(32)) == "ipE2qg=="


def test_streaming_in_blocks_equals_one_pass_and_the_compiled_extender() -> None:
    data = bytes(range(256)) * 9000  # more than two 1 MiB blocks
    assert crc32c_of(io.BytesIO(data)) == crc32c_of_bytes(data)
    pieces = 0
    for i in range(0, len(data), 100_000):
        pieces = update(pieces, data[i : i + 100_000])
    assert crc32c.to_base64(pieces) == crc32c_of_bytes(data)
    assert crc32c.extend(0, data) == update(0, data)
