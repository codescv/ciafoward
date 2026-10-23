"""
Pure-Python Nintendo 3DS `IVFC` (RomFS) Builder, Extractor & Merkle Tree Verifier.

Physical layout of a 3DS RomFS (`IVFC`) image:
  1. `0x0000 .. 0x1000`: `0x5C`-byte IVFC Header (padded to `0x60`) + Master Hashes, padded to `0x1000`
  2. `0x1000 .. 0x1000 + len(l3_padded)`: Level 3 Data (RomFS Header + Dir/File Hash & Meta Tables + File Data)
  3. `... + len(l1_padded)`: Level 1 Hash Block (SHA-256 of each `0x1000` chunk of Level 2)
  4. `... + len(l2_padded)`: Level 2 Hash Block (SHA-256 of each `0x1000` chunk of Level 3)
"""

from __future__ import annotations

import hashlib
import struct
from typing import Any, Dict, List


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _pad_bytes(data: bytes, alignment: int, pad: bytes = b"\x00") -> bytes:
    rem = len(data) % alignment
    return data if rem == 0 else data + pad * (alignment - rem)


def get_hash_table_bucket_count(count: int) -> int:
    """Compute the prime bucket count used by 3DS RomFS directory and file hash tables."""
    if count < 3:
        return 3
    if count < 19:
        return count | 1
    while (
        count % 2 == 0
        or count % 3 == 0
        or count % 5 == 0
        or count % 7 == 0
        or count % 11 == 0
        or count % 13 == 0
        or count % 17 == 0
    ):
        count += 1
    return count


def calc_romfs_hash(parent_offset: int, name_utf16le: bytes) -> int:
    """Compute the 32-bit 3DS RomFS path hash for a directory or file entry."""
    h = (parent_offset ^ 123456789) & 0xFFFFFFFF
    for i in range(0, len(name_utf16le), 2):
        ch = name_utf16le[i] | (name_utf16le[i + 1] << 8)
        h = ((h >> 5) | (h << 27)) & 0xFFFFFFFF
        h = (h ^ ch) & 0xFFFFFFFF
    return h


def build_romfs(files: Dict[str, bytes]) -> bytes:
    """
    Build a complete 3DS `IVFC` RomFS image from a dictionary mapping relative paths
    (e.g. `"rom/rom.bin"`, `"config.ini"`) to raw file bytes.
    Calling `build_romfs({})` produces the exact 0x4000-byte empty RomFS image.
    """
    dirs: List[Dict[str, Any]] = [
        {"name": "", "parent": 0, "children_dirs": [], "files": []}
    ]
    dir_map: Dict[str, int] = {"": 0}

    def ensure_dir(path_parts: List[str]) -> int:
        cur_path = ""
        parent_idx = 0
        for part in path_parts:
            cur_path = f"{cur_path}/{part}" if cur_path else part
            if cur_path not in dir_map:
                idx = len(dirs)
                dirs.append(
                    {
                        "name": part,
                        "parent": parent_idx,
                        "children_dirs": [],
                        "files": [],
                    }
                )
                dirs[parent_idx]["children_dirs"].append(idx)
                dir_map[cur_path] = idx
            parent_idx = dir_map[cur_path]
        return parent_idx

    all_dir_parts: Dict[str, List[str]] = {}
    cleaned_files: List[Tuple[List[str], bytes]] = []
    for raw_path, data in files.items():
        clean = raw_path.strip("/").replace("\\", "/")
        if not clean:
            continue
        parts = [p for p in clean.split("/") if p]
        cleaned_files.append((parts, data))
        for depth in range(1, len(parts)):
            sub = parts[:depth]
            all_dir_parts["/".join(sub)] = sub

    for _, d_parts in sorted(
        all_dir_parts.items(), key=lambda kv: (len(kv[1]), [p.upper() for p in kv[1]])
    ):
        ensure_dir(d_parts)

    for parts, data in sorted(
        cleaned_files, key=lambda item: [p.upper() for p in item[0]]
    ):
        d_idx = ensure_dir(parts[:-1])
        dirs[d_idx]["files"].append((parts[-1], data))

    dir_offsets: List[int] = []
    cur_d_off = 0
    for d in dirs:
        dir_offsets.append(cur_d_off)
        name_u16 = d["name"].encode("utf-16le")
        cur_d_off += 0x18 + _align_up(len(name_u16), 4)
    total_dir_mt_len = cur_d_off

    file_entries: List[Dict[str, Any]] = []
    dir_first_file_off: Dict[int, int] = {}
    cur_f_mt_off = 0
    cur_f_data_off = 0
    for d_idx, d in enumerate(dirs):
        f_list = d["files"]
        if not f_list:
            dir_first_file_off[d_idx] = 0xFFFFFFFF
            continue
        first_idx = len(file_entries)
        for fname, fdata in f_list:
            name_u16 = fname.encode("utf-16le")
            entry_size = 0x20 + _align_up(len(name_u16), 4)
            cur_f_data_off = _align_up(cur_f_data_off, 0x10)
            file_entries.append(
                {
                    "name": fname,
                    "name_u16": name_u16,
                    "parent_dir_off": dir_offsets[d_idx],
                    "mt_off": cur_f_mt_off,
                    "data_off": cur_f_data_off,
                    "data": fdata,
                    "next_sibling_off": 0xFFFFFFFF,
                }
            )
            cur_f_mt_off += entry_size
            cur_f_data_off += len(fdata)
        dir_first_file_off[d_idx] = file_entries[first_idx]["mt_off"]
        for i in range(first_idx, len(file_entries) - 1):
            file_entries[i]["next_sibling_off"] = file_entries[i + 1]["mt_off"]
    total_file_mt_len = cur_f_mt_off

    dir_buckets = get_hash_table_bucket_count(len(dirs))
    dir_ht = [0xFFFFFFFF] * dir_buckets
    dir_mt = bytearray(total_dir_mt_len)

    for d_idx, d in enumerate(dirs):
        my_off = dir_offsets[d_idx]
        parent_off = dir_offsets[d["parent"]]
        sibling_off = 0xFFFFFFFF
        if d_idx != 0:
            siblings = dirs[d["parent"]]["children_dirs"]
            pos = siblings.index(d_idx)
            if pos + 1 < len(siblings):
                sibling_off = dir_offsets[siblings[pos + 1]]
        first_child_off = (
            dir_offsets[d["children_dirs"][0]] if d["children_dirs"] else 0xFFFFFFFF
        )
        first_file_off = dir_first_file_off[d_idx]
        name_u16 = d["name"].encode("utf-16le")
        b_idx = calc_romfs_hash(parent_off, name_u16) % dir_buckets
        next_bucket_off = dir_ht[b_idx]
        dir_ht[b_idx] = my_off
        struct.pack_into(
            "<6I",
            dir_mt,
            my_off,
            parent_off,
            sibling_off,
            first_child_off,
            first_file_off,
            next_bucket_off,
            len(name_u16),
        )
        dir_mt[my_off + 0x18 : my_off + 0x18 + len(name_u16)] = name_u16

    file_buckets = get_hash_table_bucket_count(len(file_entries))
    file_ht = [0xFFFFFFFF] * file_buckets
    file_mt = bytearray(total_file_mt_len)
    file_data = bytearray()

    for fe in file_entries:
        my_off = fe["mt_off"]
        parent_off = fe["parent_dir_off"]
        name_u16 = fe["name_u16"]
        b_idx = calc_romfs_hash(parent_off, name_u16) % file_buckets
        next_bucket_off = file_ht[b_idx]
        file_ht[b_idx] = my_off
        struct.pack_into(
            "<IIQQII",
            file_mt,
            my_off,
            parent_off,
            fe["next_sibling_off"],
            fe["data_off"],
            len(fe["data"]),
            next_bucket_off,
            len(name_u16),
        )
        file_mt[my_off + 0x20 : my_off + 0x20 + len(name_u16)] = name_u16
        if len(file_data) < fe["data_off"]:
            file_data.extend(b"\x00" * (fe["data_off"] - len(file_data)))
        file_data.extend(fe["data"])

    dir_ht_bytes = struct.pack(f"<{dir_buckets}I", *dir_ht)
    file_ht_bytes = struct.pack(f"<{file_buckets}I", *file_ht)

    dir_ht_off = 0x28
    dir_mt_off = dir_ht_off + len(dir_ht_bytes)
    file_ht_off = dir_mt_off + len(dir_mt)
    file_mt_off = file_ht_off + len(file_ht_bytes)
    file_data_off = _align_up(file_mt_off + len(file_mt), 0x10)

    l3_hdr = struct.pack(
        "<10I",
        0x28,
        dir_ht_off,
        len(dir_ht_bytes),
        dir_mt_off,
        len(dir_mt),
        file_ht_off,
        len(file_ht_bytes),
        file_mt_off,
        len(file_mt),
        file_data_off,
    )
    l3_meta = _pad_bytes(
        l3_hdr + dir_ht_bytes + bytes(dir_mt) + file_ht_bytes + bytes(file_mt), 0x10
    )
    l3_raw = l3_meta + bytes(file_data)
    l3_padded = _pad_bytes(l3_raw, 0x1000)

    l2_raw = b"".join(
        hashlib.sha256(l3_padded[i : i + 0x1000]).digest()
        for i in range(0, len(l3_padded), 0x1000)
    )
    l2_padded = _pad_bytes(l2_raw, 0x1000)

    l1_raw = b"".join(
        hashlib.sha256(l2_padded[i : i + 0x1000]).digest()
        for i in range(0, len(l2_padded), 0x1000)
    )
    l1_padded = _pad_bytes(l1_raw, 0x1000)

    master_hashes = b"".join(
        hashlib.sha256(l1_padded[i : i + 0x1000]).digest()
        for i in range(0, len(l1_padded), 0x1000)
    )

    ivfc_hdr = bytearray(0x60)
    struct.pack_into("<4sII", ivfc_hdr, 0x00, b"IVFC", 0x00010000, len(master_hashes))
    struct.pack_into("<QQII", ivfc_hdr, 0x0C, 0, len(l1_raw), 12, 0)
    struct.pack_into("<QQII", ivfc_hdr, 0x24, len(l1_padded), len(l2_raw), 12, 0)
    struct.pack_into(
        "<QQII", ivfc_hdr, 0x3C, len(l1_padded) + len(l2_padded), len(l3_raw), 12, 0
    )
    struct.pack_into("<II", ivfc_hdr, 0x54, 0x5C, 0)
    block0 = _pad_bytes(bytes(ivfc_hdr) + master_hashes, 0x1000)

    return block0 + l3_padded + l1_padded + l2_padded


def extract_romfs(romfs_bytes: bytes) -> Dict[str, bytes]:
    """Extract all files from a 3DS `IVFC` RomFS image as `{relative_path: raw_bytes}`."""
    if len(romfs_bytes) < 0x1000 or romfs_bytes[:4] != b"IVFC":
        raise ValueError("Invalid IVFC header in RomFS")
    (master_hash_sz,) = struct.unpack_from("<I", romfs_bytes, 0x08)
    l3_off = _align_up(0x60 + master_hash_sz, 0x1000)
    l3 = romfs_bytes[l3_off:]
    (
        hdr_len,
        _d_ht_o,
        _d_ht_l,
        d_mt_o,
        d_mt_l,
        _f_ht_o,
        _f_ht_l,
        f_mt_o,
        f_mt_l,
        f_data_o,
    ) = struct.unpack_from("<10I", l3, 0)
    if hdr_len != 0x28:
        raise ValueError(f"Unexpected RomFS Level 3 header size: {hdr_len:#x}")
    dir_mt = l3[d_mt_o : d_mt_o + d_mt_l]
    file_mt = l3[f_mt_o : f_mt_o + f_mt_l]
    out: Dict[str, bytes] = {}

    def walk_dir(d_off: int, prefix: str) -> None:
        _p_off, _sib_off, child_off, file_off, _bucket_off, name_len = (
            struct.unpack_from("<6I", dir_mt, d_off)
        )
        name = dir_mt[d_off + 0x18 : d_off + 0x18 + name_len].decode(
            "utf-16le", errors="replace"
        )
        cur_path = f"{prefix}/{name}" if name else prefix

        f_off = file_off
        while f_off != 0xFFFFFFFF and f_off < len(file_mt):
            _fp_off, fsib_off, data_off, data_len, _fbucket_off, fname_len = (
                struct.unpack_from("<IIQQII", file_mt, f_off)
            )
            fname = file_mt[f_off + 0x20 : f_off + 0x20 + fname_len].decode(
                "utf-16le", errors="replace"
            )
            fpath = f"{cur_path}/{fname}".lstrip("/")
            out[fpath] = l3[f_data_o + data_off : f_data_o + data_off + data_len]
            f_off = fsib_off

        c_off = child_off
        while c_off != 0xFFFFFFFF and c_off < len(dir_mt):
            _, csib_off, _, _, _, _ = struct.unpack_from("<6I", dir_mt, c_off)
            walk_dir(c_off, cur_path)
            c_off = csib_off

    if len(dir_mt) >= 0x18:
        walk_dir(0, "")
    return out


def verify_romfs_ivfc(romfs_bytes: bytes) -> bool:
    """Verify the IVFC Master Hash, Level 1, Level 2, and Level 3 SHA-256 tree."""
    if len(romfs_bytes) < 0x4000 or romfs_bytes[:4] != b"IVFC":
        return False
    _, ver, master_hash_sz = struct.unpack_from("<4sII", romfs_bytes, 0x00)
    if ver != 0x00010000:
        return False
    _, l1_sz, l1_blk, _ = struct.unpack_from("<QQII", romfs_bytes, 0x0C)
    _, l2_sz, l2_blk, _ = struct.unpack_from("<QQII", romfs_bytes, 0x24)
    _, l3_sz, l3_blk, _ = struct.unpack_from("<QQII", romfs_bytes, 0x3C)
    if (l1_blk, l2_blk, l3_blk) != (12, 12, 12):
        return False

    master_hashes = romfs_bytes[0x60 : 0x60 + master_hash_sz]
    l3_off = _align_up(0x60 + master_hash_sz, 0x1000)
    l3_padded_len = _align_up(l3_sz, 0x1000)
    l1_off = l3_off + l3_padded_len
    l1_padded_len = _align_up(l1_sz, 0x1000)
    l2_off = l1_off + l1_padded_len
    l2_padded_len = _align_up(l2_sz, 0x1000)

    if len(romfs_bytes) < l2_off + l2_padded_len:
        return False

    l3_padded = romfs_bytes[l3_off : l3_off + l3_padded_len]
    l1_padded = romfs_bytes[l1_off : l1_off + l1_padded_len]
    l2_padded = romfs_bytes[l2_off : l2_off + l2_padded_len]

    expected_l2 = b"".join(
        hashlib.sha256(l3_padded[i : i + 0x1000]).digest()
        for i in range(0, len(l3_padded), 0x1000)
    )
    if l2_padded[: len(expected_l2)] != expected_l2:
        return False

    expected_l1 = b"".join(
        hashlib.sha256(l2_padded[i : i + 0x1000]).digest()
        for i in range(0, len(l2_padded), 0x1000)
    )
    if l1_padded[: len(expected_l1)] != expected_l1:
        return False

    expected_master = b"".join(
        hashlib.sha256(l1_padded[i : i + 0x1000]).digest()
        for i in range(0, len(l1_padded), 0x1000)
    )
    return master_hashes == expected_master
