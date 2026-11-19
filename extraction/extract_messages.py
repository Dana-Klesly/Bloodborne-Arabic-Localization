"""Read-only extractor for Bloodborne's English item/menu FMG message archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
import zlib
from pathlib import Path


def dcx_decompress(data: bytes) -> bytes:
    if not data.startswith(b"DCX\0"):
        raise ValueError("Expected a DCX-compressed archive")
    marker = data.find(b"DCA\0")
    if marker < 0:
        raise ValueError("DCX payload marker not found")
    # Bloodborne PS4 DCX stores a four-byte field between DCA and its zlib stream.
    return zlib.decompress(data[marker + 8 :])


def _reverse_bits(value: int) -> int:
    return int(f"{value:08b}"[::-1], 2)


def _cstring(data: bytes, offset: int, encoding: str) -> str:
    if encoding == "utf-16-le":
        end = offset
        while end + 1 < len(data) and data[end : end + 2] != b"\0\0":
            end += 2
        if end + 1 >= len(data):
            raise ValueError(f"Unterminated UTF-16 path at {offset:#x}")
        return data[offset:end].decode(encoding)
    end = data.find(b"\0", offset)
    if end < 0:
        raise ValueError(f"Unterminated path at {offset:#x}")
    return data[offset:end].decode(encoding)


def bnd4_entries(data: bytes) -> list[tuple[int, str, bytes]]:
    if data[:4] != b"BND4":
        raise ValueError(f"Expected BND4, got {data[:4]!r}")
    big_endian = data[9] != 0
    endian = ">" if big_endian else "<"
    bit_big_endian = data[10] == 0
    count = struct.unpack_from(endian + "I", data, 0x0C)[0]
    header_size = struct.unpack_from(endian + "Q", data, 0x20)[0]
    unicode_paths = data[0x30] != 0
    binder_flags = data[0x31]
    if not bit_big_endian:
        binder_flags = _reverse_bits(binder_flags)
    has_ids = bool(binder_flags & 0x02)
    has_names = bool(binder_flags & 0x0C)
    has_long_offsets = bool(binder_flags & 0x10)
    has_compression = bool(binder_flags & 0x20)
    path_encoding = "utf-16-le" if unicode_paths else "shift_jis"
    if header_size < 16 or count > 100_000:
        raise ValueError("Implausible BND4 header")

    entries = []
    cursor = 0x40
    for _ in range(count):
        raw_flags = data[cursor]
        entry_flags = raw_flags if bit_big_endian else _reverse_bits(raw_flags)
        if data[cursor + 1 : cursor + 4] != b"\0\0\0":
            raise ValueError(f"Unexpected BND4 entry padding at {cursor:#x}")
        if struct.unpack_from(endian + "i", data, cursor + 4)[0] != -1:
            raise ValueError(f"Unexpected BND4 entry marker at {cursor:#x}")
        pos = cursor + 8
        packed_size = struct.unpack_from(endian + "Q", data, pos)[0]
        pos += 8
        unpacked_size = None
        if has_compression:
            unpacked_size = struct.unpack_from(endian + "Q", data, pos)[0]
            pos += 8
        offset_format = "Q" if has_long_offsets else "I"
        data_offset = struct.unpack_from(endian + offset_format, data, pos)[0]
        pos += struct.calcsize(offset_format)
        entry_id = struct.unpack_from(endian + "i", data, pos)[0] if has_ids else _
        pos += 4 if has_ids else 0
        path_offset = struct.unpack_from(endian + "I", data, pos)[0] if has_names else 0
        pos += 4 if has_names else 0
        if pos - cursor != header_size:
            raise ValueError(f"BND4 entry header size mismatch at {cursor:#x}")
        path = _cstring(data, path_offset, path_encoding) if has_names else f"entry_{entry_id}"
        packed = data[data_offset : data_offset + packed_size]
        if len(packed) != packed_size:
            raise ValueError(f"Truncated BND4 entry {path!r}")
        content = zlib.decompress(packed) if entry_flags & 1 else packed
        if unpacked_size is not None and len(content) != unpacked_size:
            raise ValueError(f"Unpacked size mismatch for {path!r}")
        entries.append((entry_id, path, content))
        cursor += header_size
    return entries


def fmg_entries(data: bytes) -> dict[int, str]:
    if len(data) < 40 or data[2] != 2:
        raise ValueError("Expected a Bloodborne FMG version 2 file")
    big_endian = bool(data[1])
    endian = ">" if big_endian else "<"
    declared_size = struct.unpack_from(endian + "I", data, 4)[0]
    if declared_size != len(data) or data[8] != 1 or data[9] != 0:
        raise ValueError("FMG header validation failed")
    range_count, string_count = struct.unpack_from(endian + "II", data, 12)
    offsets_pos = struct.unpack_from(endian + "Q", data, 24)[0]
    if range_count > 100_000 or string_count > 1_000_000:
        raise ValueError("Implausible FMG entry counts")
    ranges = []
    pos = 40
    for _ in range(range_count):
        first_index, first_id, last_id = struct.unpack_from(endian + "iii", data, pos)
        if data[pos + 12 : pos + 16] != b"\0" * 4 or last_id < first_id:
            raise ValueError("Malformed FMG ID range")
        ranges.append((first_index, first_id, last_id))
        pos += 16
    if offsets_pos != pos:
        raise ValueError("FMG pointer table offset does not match its ranges")
    pointers = struct.unpack_from(endian + f"{string_count}Q", data, offsets_pos)
    result = {}
    for first_index, first_id, last_id in ranges:
        for index, string_id in enumerate(range(first_id, last_id + 1), first_index):
            if index >= len(pointers) or string_id in result:
                raise ValueError("Malformed FMG string index")
            string_offset = pointers[index]
            if string_offset == 0:
                text = ""
            else:
                end = string_offset
                while end + 1 < len(data) and data[end : end + 2] != b"\0\0":
                    end += 2
                if end + 1 >= len(data):
                    raise ValueError(f"Unterminated FMG text at {string_offset:#x}")
                text = data[string_offset:end].decode("utf-16-be" if big_endian else "utf-16-le")
            result[string_id] = text
    if len(result) != string_count:
        raise ValueError("FMG string count does not match its ID ranges")
    return result


def category_for_fmg(fmg_name: str) -> str:
    name = fmg_name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if any(token in name for token in ("会話", "ダイアログ", "dialog", "イベントテキスト")):
        return "dialogue"
    if any(token in name for token in ("システムメッセージ", "死亡因", "死因")):
        return "system"
    if any(token in name for token in ("npc名", "地名")):
        return "place"
    if any(token in name for token in ("説明", "うんちく", "効果")):
        return "item_desc"
    if "名" in name:
        return "item_name"
    return "ui"


def extract(archive: Path, game_root: Path) -> list[dict[str, str]]:
    relative_archive = archive.relative_to(game_root).as_posix()
    locale = archive.parent.name
    records = []
    decompressed = dcx_decompress(archive.read_bytes())
    for bnd_entry_id, fmg_name, fmg_data in bnd4_entries(decompressed):
        if not fmg_name.lower().endswith(".fmg"):
            continue
        stem = fmg_name.replace("\\", "/").rsplit("/", 1)[-1]
        if stem.lower().endswith(".fmg"):
            stem = stem[:-4]
        category = category_for_fmg(fmg_name)
        for string_id, source in fmg_entries(fmg_data).items():
            if not source:
                continue
            string_key = f"bb:{locale}:{category}:bnd{bnd_entry_id}:{string_id}"
            records.append({
                "id": string_key,
                "source": source,
                "context": f"FMG group: {stem}",
                "type": category,
                "source_file": relative_archive,
                "fmg_name": fmg_name,
                "bnd_entry_id": str(bnd_entry_id),
                "entry_id": str(string_id),
            })
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("game_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    game_root = args.game_root.resolve()
    archive_root = game_root / "CUSA03173" / "dvdroot_ps4" / "msg" / "engus"
    archives = [archive_root / "item.msgbnd.dcx", archive_root / "menu.msgbnd.dcx"]
    missing = [str(path) for path in archives if not path.is_file()]
    if missing:
        raise FileNotFoundError("Required English message archive missing: " + ", ".join(missing))

    records = []
    manifest_archives = []
    for archive in archives:
        raw = archive.read_bytes()
        rows = extract(archive, game_root)
        records.extend(rows)
        manifest_archives.append({
            "source_file": archive.relative_to(game_root).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "nonempty_strings": len(rows),
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "id", "source", "context", "type", "source_file", "fmg_name", "bnd_entry_id", "entry_id",
        ], lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)
    manifest = {
        "status": "extracted_pending_translation",
        "locale": "engus",
        "strings": len(records),
        "archives": manifest_archives,
        "translation_status": "not_started",
    }
    args.output.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
