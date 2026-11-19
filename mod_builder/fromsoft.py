"""Read and write Bloodborne's message archives: DCX (DFLT/zlib) -> BND4 -> FMG version 2.

Same layout as the game files and as bbport's scripts/quit_menu.py:
- DCX: 0x4C-byte header, big-endian uncompressed/compressed sizes at 0x1C, zlib stream after.
- BND4: 0x24-byte entries (size +0x08, unpacked size +0x10, data offset +0x18, id +0x1C),
  file data 16-byte aligned, stored uncompressed.
- FMG v2: id ranges at 0x28 (16 bytes each), 64-bit string offsets, UTF-16LE strings;
  offset 0 means "no string" (None).
"""
from __future__ import annotations

import struct
import zlib


# ---------- DCX ----------
def unpack_dcx(data: bytes) -> bytes:
    if data[:4] != b"DCX\0" or data[0x28:0x2C] != b"DFLT":
        raise ValueError("not a DFLT DCX archive")
    body = zlib.decompress(data[0x4C:])
    size = struct.unpack_from(">I", data, 0x1C)[0]
    if size != len(body):
        raise ValueError("DCX uncompressed size mismatch")
    return body


def pack_dcx(template: bytes, body: bytes) -> bytes:
    packed = zlib.compress(body, 9)
    header = bytearray(template[:0x4C])
    struct.pack_into(">II", header, 0x1C, len(body), len(packed))
    return bytes(header) + packed


# ---------- BND4 ----------
def bnd4_files(data: bytes) -> list[tuple[int, int, int, int]]:
    """[(entry offset, data offset, size, file id)] of a BND4 with 0x24-byte entries."""
    if data[:4] != b"BND4":
        raise ValueError("not a BND4 archive")
    count = struct.unpack_from("<I", data, 0x0C)[0]
    size = struct.unpack_from("<Q", data, 0x20)[0]
    if size != 0x24:
        raise ValueError(f"unsupported BND4 entry size {size:#x}")
    out = []
    for i in range(count):
        entry = 0x40 + i * size
        flags = data[entry]
        if flags & 0x01 or flags & 0x80:  # compressed entry (either bit order)
            raise ValueError("compressed BND4 entries are not supported")
        length, unpacked = struct.unpack_from("<QQ", data, entry + 0x08)
        if length != unpacked:
            raise ValueError("BND4 entry with differing packed/unpacked size")
        offset, file_id = struct.unpack_from("<II", data, entry + 0x18)
        out.append((entry, offset, length, file_id))
    return out


def bnd4_contents(data: bytes) -> dict[int, bytes]:
    return {fid: data[off:off + ln] for _, off, ln, fid in bnd4_files(data)}


def rebuild_bnd4(data: bytes, replace: dict[int, bytes]) -> bytes:
    """The BND4 with files {id: bytes} replaced; everything else kept, data re-laid out in the
    original order with the original 16-byte alignment."""
    files = bnd4_files(data)
    unknown = set(replace) - {f[3] for f in files}
    if unknown:
        raise ValueError(f"BND4 has no file ids {sorted(unknown)}")
    start = min(offset for _, offset, _, _ in files)
    out = bytearray(data[:start])
    for entry, offset, length, file_id in sorted(files, key=lambda f: f[1]):
        body = replace.get(file_id, data[offset:offset + length])
        out += b"\0" * (-len(out) % 16)
        struct.pack_into("<QQI", out, entry + 0x08, len(body), len(body), len(out))
        out += body
    return bytes(out)


# ---------- FMG ----------
def read_fmg(data: bytes) -> tuple[list[tuple[int, int, int]], dict[int, str | None]]:
    """(groups [(first index, first id, last id)], {id: text or None})."""
    if len(data) < 0x28 or data[2] != 2 or data[1] != 0:
        raise ValueError("expected a little-endian FMG version 2")
    if struct.unpack_from("<I", data, 4)[0] != len(data):
        raise ValueError("FMG size field mismatch")
    ngroups, nstrings = struct.unpack_from("<II", data, 0x0C)
    offsets = struct.unpack_from("<Q", data, 0x18)[0]
    if offsets != 0x28 + 16 * ngroups:
        raise ValueError("FMG offset table not after its groups")
    groups, strings = [], {}
    for g in range(ngroups):
        index, first, last, pad = struct.unpack_from("<iiii", data, 0x28 + 16 * g)
        if pad or last < first:
            raise ValueError("malformed FMG group")
        groups.append((index, first, last))
        for k, sid in enumerate(range(first, last + 1)):
            at = struct.unpack_from("<q", data, offsets + 8 * (index + k))[0]
            if sid in strings:
                raise ValueError("duplicate FMG id")
            if at <= 0:
                strings[sid] = None
                continue
            end = at
            while data[end:end + 2] != b"\0\0":
                end += 2
                if end >= len(data):
                    raise ValueError("unterminated FMG string")
            strings[sid] = data[at:end].decode("utf-16-le")
    if len(strings) != nstrings:
        raise ValueError("FMG string count mismatch")
    return groups, strings


def write_fmg(template: bytes, groups, strings: dict) -> bytes:
    """An FMG with the template's header flags and the SAME id groups (ids are never added or
    removed), holding `strings` ({id: text or None}). groups=None regroups all ids in runs
    (only used when ids are added, like bbport's Quit to Desktop help text)."""
    if groups is None:
        groups = []
        for sid in sorted(strings):
            if groups and groups[-1][2] == sid - 1:
                groups[-1] = (groups[-1][0], groups[-1][1], sid)
            else:
                groups.append((sum(g[2] - g[1] + 1 for g in groups), sid, sid))
    ids = [sid for _, first, last in groups for sid in range(first, last + 1)]
    if set(ids) != set(strings):
        raise ValueError("write_fmg: ids differ from the template groups")
    offsets_at = 0x28 + 16 * len(groups)
    text_at = offsets_at + 8 * len(ids)
    out = bytearray(template[:0x28])
    struct.pack_into("<I", out, 0x0C, len(groups))
    struct.pack_into("<I", out, 0x10, len(ids))
    struct.pack_into("<Q", out, 0x18, offsets_at)
    for index, first, last in groups:
        out += struct.pack("<iiii", index, first, last, 0)
    order = [None] * len(ids)
    for index, first, last in groups:
        for k, sid in enumerate(range(first, last + 1)):
            order[index + k] = sid
    texts = bytearray()
    slots = []
    for sid in order:
        text = strings[sid]
        if text is None:
            slots.append(0)
            continue
        slots.append(text_at + len(texts))
        texts += text.encode("utf-16-le") + b"\0\0"
    for slot in slots:
        out += struct.pack("<q", slot)
    out += texts
    out += b"\0" * (4 + (-(len(out) + 4) % 4))  # the game's files end in zeros like this
    struct.pack_into("<I", out, 0x04, len(out))
    return bytes(out)
