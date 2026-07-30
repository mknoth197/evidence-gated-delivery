#!/usr/bin/env python3
"""Bounded structural validation for runtime visual evidence artifacts."""
from __future__ import annotations

import struct
import zlib


def valid_png(data: bytes) -> bool:
    """Strictly parse a non-interlaced PNG and its decompressed scanlines."""

    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    width = height = bit_depth = color_type = None
    compressed = bytearray()
    saw_iend = False
    saw_ihdr = False
    saw_idat = False
    idat_ended = False
    saw_plte = False
    palette_entries = 0
    singleton_ancillary: set[bytes] = set()
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        if (
            len(chunk_type) != 4
            or not all(
                ord("A") <= byte <= ord("Z")
                or ord("a") <= byte <= ord("z")
                for byte in chunk_type
            )
            or chunk_type[2] & 0x20
        ):
            return False
        end = offset + 12 + length
        if end > len(data):
            return False
        payload = data[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", data[offset + 8 + length : end])[0]
        if zlib.crc32(chunk_type + payload) & 0xFFFFFFFF != expected_crc:
            return False
        if chunk_type == b"IHDR":
            if offset != 8 or length != 13 or saw_ihdr:
                return False
            saw_ihdr = True
            (
                width,
                height,
                bit_depth,
                color_type,
                compression,
                filtering,
                interlace,
            ) = struct.unpack(">IIBBBBB", payload)
            if (
                width < 1
                or height < 1
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                return False
        elif chunk_type == b"IDAT":
            if not saw_ihdr or idat_ended:
                return False
            saw_idat = True
            if len(compressed) + len(payload) > 50 * 1024 * 1024:
                return False
            compressed.extend(payload)
        elif chunk_type == b"IEND":
            if length != 0 or end != len(data) or not saw_idat:
                return False
            saw_iend = True
            break
        elif chunk_type == b"PLTE":
            if (
                saw_plte
                or saw_idat
                or b"tRNS" in singleton_ancillary
                or color_type in {0, 4}
                or length < 3
                or length > 768
                or length % 3
            ):
                return False
            saw_plte = True
            palette_entries = length // 3
        elif chunk_type == b"tRNS":
            if (
                chunk_type in singleton_ancillary
                or saw_idat
                or color_type not in {0, 2, 3}
                or (color_type == 0 and length != 2)
                or (color_type == 2 and length != 6)
                or (
                    color_type == 3
                    and (
                        not saw_plte
                        or length < 1
                        or length > palette_entries
                    )
                )
            ):
                return False
            singleton_ancillary.add(chunk_type)
        elif chunk_type in {b"cHRM", b"gAMA", b"sRGB", b"pHYs", b"tIME"}:
            if chunk_type in singleton_ancillary:
                return False
            singleton_ancillary.add(chunk_type)
            if chunk_type == b"cHRM" and (
                saw_plte or saw_idat or length != 32
            ):
                return False
            if chunk_type == b"gAMA" and (
                saw_plte
                or saw_idat
                or length != 4
                or struct.unpack(">I", payload)[0] == 0
            ):
                return False
            if chunk_type == b"sRGB" and (
                saw_plte
                or saw_idat
                or length != 1
                or payload[0] > 3
            ):
                return False
            if chunk_type == b"pHYs" and (
                saw_idat or length != 9 or payload[8] > 1
            ):
                return False
            if chunk_type == b"tIME":
                if length != 7:
                    return False
                year, month, day, hour, minute, second = struct.unpack(
                    ">HBBBBB", payload
                )
                if not (
                    year >= 1
                    and 1 <= month <= 12
                    and 1 <= day <= 31
                    and hour <= 23
                    and minute <= 59
                    and second <= 60
                ):
                    return False
        elif chunk_type and chunk_type[0] & 0x20 == 0:
            return False
        elif chunk_type:
            return False
        if saw_idat and chunk_type not in {b"IDAT", b"IEND"}:
            idat_ended = True
        offset = end
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type)
    allowed_depths = {
        0: {1, 2, 4, 8, 16},
        2: {8, 16},
        3: {1, 2, 4, 8},
        4: {8, 16},
        6: {8, 16},
    }
    if (
        not saw_iend
        or channels is None
        or bit_depth not in allowed_depths.get(color_type, set())
        or not compressed
        or (color_type == 3 and not saw_plte)
        or (color_type == 3 and palette_entries > 2 ** int(bit_depth))
    ):
        return False
    row_bytes = (int(width) * channels * int(bit_depth) + 7) // 8
    expected_size = int(height) * (row_bytes + 1)
    if expected_size > 100 * 1024 * 1024:
        return False
    try:
        decompressor = zlib.decompressobj()
        scanlines = decompressor.decompress(bytes(compressed), expected_size + 1)
    except zlib.error:
        return False
    if (
        len(scanlines) != expected_size
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or not decompressor.eof
    ):
        return False
    bytes_per_pixel = max(1, (channels * int(bit_depth) + 7) // 8)
    previous = bytearray(row_bytes)
    reconstructed_rows: list[bytes] = []
    for row in range(int(height)):
        start = row * (row_bytes + 1)
        filter_type = scanlines[start]
        if filter_type not in range(5):
            return False
        raw = scanlines[start + 1 : start + 1 + row_bytes]
        reconstructed = bytearray(row_bytes)
        for index, value in enumerate(raw):
            left = (
                reconstructed[index - bytes_per_pixel]
                if index >= bytes_per_pixel
                else 0
            )
            above = previous[index]
            upper_left = (
                previous[index - bytes_per_pixel]
                if index >= bytes_per_pixel
                else 0
            )
            if filter_type == 0:
                predictor = 0
            elif filter_type == 1:
                predictor = left
            elif filter_type == 2:
                predictor = above
            elif filter_type == 3:
                predictor = (left + above) // 2
            else:
                estimate = left + above - upper_left
                distances = (
                    abs(estimate - left),
                    abs(estimate - above),
                    abs(estimate - upper_left),
                )
                predictor = (left, above, upper_left)[
                    distances.index(min(distances))
                ]
            reconstructed[index] = (value + predictor) & 0xFF
        reconstructed_rows.append(bytes(reconstructed))
        previous = reconstructed
    if color_type == 3:
        mask = (1 << int(bit_depth)) - 1
        for row in reconstructed_rows:
            for pixel in range(int(width)):
                bit_offset = pixel * int(bit_depth)
                byte = row[bit_offset // 8]
                shift = 8 - int(bit_depth) - (bit_offset % 8)
                if (byte >> shift) & mask >= palette_entries:
                    return False
    return True
