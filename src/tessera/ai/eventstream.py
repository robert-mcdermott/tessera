"""The AWS event stream framing that Bedrock's ConverseStream answers with, from the standard
library: each message is a prelude (total length, headers length, and a CRC-32 of the two), its
headers, its payload, and a CRC-32 of everything before it."""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable, Iterator
from typing import Any


class EventStreamError(ValueError):
    """A frame that is not what it says it is."""


def _headers(data: bytes) -> dict[str, Any]:
    out: dict[str, Any] = {}
    i = 0
    while i < len(data):
        size = data[i]
        name = data[i + 1 : i + 1 + size].decode("utf-8")
        i += 1 + size
        kind = data[i]
        i += 1
        if kind in (0, 1):  # true, false
            value: Any = kind == 0
        elif kind == 2:
            value, i = data[i], i + 1
        elif kind == 3:
            (value,), i = struct.unpack(">h", data[i : i + 2]), i + 2
        elif kind == 4:
            (value,), i = struct.unpack(">i", data[i : i + 4]), i + 4
        elif kind in (5, 8):  # long, timestamp
            (value,), i = struct.unpack(">q", data[i : i + 8]), i + 8
        elif kind in (6, 7):  # bytes, string
            (length,) = struct.unpack(">H", data[i : i + 2])
            raw = data[i + 2 : i + 2 + length]
            value, i = (raw.decode("utf-8") if kind == 7 else raw), i + 2 + length
        elif kind == 9:
            value, i = data[i : i + 16].hex(), i + 16
        else:
            raise EventStreamError(f"unknown header type {kind}")
        out[name] = value
    return out


def frames(chunks: Iterable[bytes]) -> Iterator[tuple[dict[str, Any], bytes]]:
    """(headers, payload) for each message in a stream of bytes, checked."""
    buffer = b""
    for chunk in chunks:
        buffer += chunk
        while len(buffer) >= 12:
            total, header_length, prelude_crc = struct.unpack(">III", buffer[:12])
            if zlib.crc32(buffer[:8]) != prelude_crc:
                raise EventStreamError("the prelude's checksum does not match")
            if total < 16 or len(buffer) < total:
                break
            message, buffer = buffer[:total], buffer[total:]
            (message_crc,) = struct.unpack(">I", message[-4:])
            if zlib.crc32(message[:-4]) != message_crc:
                raise EventStreamError("the message's checksum does not match")
            headers = _headers(message[12 : 12 + header_length])
            yield headers, message[12 + header_length : -4]
    if buffer:
        raise EventStreamError("the stream ended inside a message")


def encode(headers: dict[str, str], payload: bytes) -> bytes:
    """One message with string headers (to build test streams)."""
    raw = b"".join(
        bytes([len(k)]) + k.encode() + b"\x07" + struct.pack(">H", len(v.encode())) + v.encode()
        for k, v in headers.items()
    )
    total = 12 + len(raw) + len(payload) + 4
    prelude = struct.pack(">II", total, len(raw))
    prelude += struct.pack(">I", zlib.crc32(prelude))
    body = prelude + raw + payload
    return body + struct.pack(">I", zlib.crc32(body))


__all__ = ["EventStreamError", "encode", "frames"]
