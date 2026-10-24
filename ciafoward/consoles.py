"""
Multi-Console ROM Parser & Container Converter for Nintendo 3DS Virtual Console / Forwarder CIAs:
  - Game Boy Advance (`gba`: `.gba`, `.agb`)
  - Game Boy (`gb`: `.gb`, `.sgb`)
  - Game Boy Color (`gbc`: `.gbc`)
  - NES / Famicom (`nes`: `.nes`)
  - Super Nintendo / Super Famicom (`snes`: `.sfc`, `.smc`)
  - Sega Genesis / Mega Drive (`md`: `.md`, `.gen`, `.smd`, `.bin`)
  - Transparent `.zip` ROM archive unpacking
"""

from __future__ import annotations

import hashlib
import io
import struct
import zipfile
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from .gba import (
    NINTENDO_LOGO_PREFIX,
    REGION_MAP,
    GBARomInfo,
    SaveTypeID,
    generate_title_id,
    parse_gba_rom,
)

if TYPE_CHECKING:
    from .graphics import RGBA, RGBAImage

GB_LOGO_PREFIX = bytes(
    [
        0xCE, 0xED, 0x66, 0x66, 0xCC, 0x0D, 0x00, 0x0B,
        0x03, 0x73, 0x00, 0x83, 0x00, 0x0C, 0x00, 0x0D,
    ]
)

SYSTEM_SPECS: Dict[str, Dict[str, object]] = {
    "gba": {
        "name": "Game Boy Advance",
        "short_label": "GAME BOY ADVANCE",
        "libretro_db": "Nintendo - Game Boy Advance",
        "extensions": (".gba", ".agb"),
        "product_prefix": "CTR-P",
        "default_publisher": "Nintendo",
        "core_version": 0x00000202,
    },
    "gb": {
        "name": "Game Boy",
        "short_label": "GAME BOY",
        "libretro_db": "Nintendo - Game Boy",
        "extensions": (".gb", ".sgb"),
        "product_prefix": "GB-P",
        "default_publisher": "Nintendo",
        "core_version": 0x00000002,
    },
    "gbc": {
        "name": "Game Boy Color",
        "short_label": "GAME BOY COLOR",
        "libretro_db": "Nintendo - Game Boy Color",
        "extensions": (".gbc",),
        "product_prefix": "GBC-P",
        "default_publisher": "Nintendo",
        "core_version": 0x00000002,
    },
    "nes": {
        "name": "NES / Famicom",
        "short_label": "NINTENDO ENTERTAINMENT SYSTEM",
        "libretro_db": "Nintendo - Nintendo Entertainment System",
        "extensions": (".nes",),
        "product_prefix": "NES-P",
        "default_publisher": "Nintendo",
        "core_version": 0x00000002,
    },
    "snes": {
        "name": "Super Nintendo",
        "short_label": "SUPER NINTENDO",
        "libretro_db": "Nintendo - Super Nintendo Entertainment System",
        "extensions": (".sfc", ".smc"),
        "product_prefix": "SNES-P",
        "default_publisher": "Nintendo",
        "core_version": 0x00000002,
    },
    "md": {
        "name": "Sega Genesis / Mega Drive",
        "short_label": "SEGA GENESIS",
        "libretro_db": "Sega - Mega Drive - Genesis",
        "extensions": (".md", ".gen", ".smd", ".bin"),
        "product_prefix": "GEN-P",
        "default_publisher": "SEGA",
        "core_version": 0x00000002,
    },
    "nds": {
        "name": "Nintendo DS",
        "short_label": "NINTENDO DS",
        "libretro_db": "Nintendo - Nintendo DS",
        "extensions": (".nds", ".srl", ".dsi", ".ids"),
        "product_prefix": "NTR-P",
        "default_publisher": "Nintendo",
        "core_version": 0x00000002,
    },
}

ALL_ROM_EXTENSIONS: Tuple[str, ...] = tuple(
    ext for spec in SYSTEM_SPECS.values() for ext in spec["extensions"]  # type: ignore[index]
)

# iNES Mapper ID -> Nintendo 3DS TNES Virtual Console Mapper ID (verified against QBAP jump tables)
INES_TO_TNES_MAPPER: Dict[int, int] = {
    0: 0,    # NROM
    1: 1,    # MMC1 (SxROM)
    2: 6,    # UxROM
    3: 7,    # CNROM
    4: 3,    # MMC3 (TxROM)
    5: 5,    # MMC5 (ExROM)
    7: 9,    # AxROM
    9: 2,    # MMC2 (PxROM)
    10: 4,   # MMC4 (FxROM)
    11: 31,  # Color Dreams
    16: 27,  # Bandai FCG
    18: 32,  # Jaleco SS88006
    19: 37,  # Namco 163
    21: 20,  # Konami VRC4a/VRC4c
    22: 21,  # Konami VRC2a
    23: 22,  # Konami VRC2b/VRC4e
    24: 25,  # Konami VRC6a
    25: 23,  # Konami VRC4b/VRC4d
    26: 26,  # Konami VRC6b
    32: 35,  # Irem G-101
    33: 39,  # Taito TC0190
    34: 47,  # BNROM
    48: 40,  # Taito TC0690
    65: 36,  # Irem H3001
    66: 8,   # GxROM
    67: 12,  # Sunsoft-3
    68: 13,  # Sunsoft-4
    69: 14,  # Sunsoft FME-7 / 5B
    70: 15,  # Bandai 74161/32
    73: 16,  # Konami VRC3
    75: 17,  # Konami VRC1
    78: 30,  # Irem 74HC161/32
    80: 41,  # Taito X1-005
    82: 42,  # Taito X1-017
    85: 19,  # Konami VRC7
    87: 11,  # Jaleco JF-05..10
    89: 34,  # Sunsoft-2 (89)
    93: 33,  # Sunsoft-2 (93)
    118: 43, # TxSROM
    119: 45, # TQROM
    152: 29, # Bandai 74161/32 (152)
    159: 27, # Bandai LZ93D50 (24C01)
    184: 10, # Sunsoft-1
    185: 28, # CNROM with protection
    206: 38, # DxROM / Namcot 108
}

GB_CARTRIDGE_TYPES: Dict[int, Tuple[str, bool, bool]] = {
    0x00: ("ROM ONLY", False, False),
    0x01: ("MBC1", False, False),
    0x02: ("MBC1+RAM", False, False),
    0x03: ("MBC1+RAM+BATTERY", True, False),
    0x05: ("MBC2", False, False),
    0x06: ("MBC2+BATTERY", True, False),
    0x08: ("ROM+RAM", False, False),
    0x09: ("ROM+RAM+BATTERY", True, False),
    0x0F: ("MBC3+TIMER+BATTERY", True, True),
    0x10: ("MBC3+TIMER+RAM+BATTERY", True, True),
    0x11: ("MBC3", False, False),
    0x12: ("MBC3+RAM", False, False),
    0x13: ("MBC3+RAM+BATTERY", True, False),
    0x19: ("MBC5", False, False),
    0x1A: ("MBC5+RAM", False, False),
    0x1B: ("MBC5+RAM+BATTERY", True, False),
    0x1C: ("MBC5+RUMBLE", False, False),
    0x1D: ("MBC5+RUMBLE+RAM", False, False),
    0x1E: ("MBC5+RUMBLE+RAM+BATTERY", True, False),
    0x20: ("MBC6", True, False),
    0x22: ("MBC7+SENSOR+RUMBLE+RAM+BATTERY", True, False),
    0xFE: ("HuC3", True, True),
    0xFF: ("HuC1+RAM+BATTERY", True, False),
}

GB_RAM_SIZES: Dict[int, int] = {
    0x00: 0,
    0x01: 2 * 1024,
    0x02: 8 * 1024,
    0x03: 32 * 1024,
    0x04: 128 * 1024,
    0x05: 64 * 1024,
}


def deinterleave_smd_if_needed(rom_bytes: bytes, ext: str = "") -> bytes:
    """
    Convert a Super Magic Drive (`.smd`) Genesis ROM (512-byte header + 16KB odd/even interleaved blocks)
    into standard linear `.md` / `.bin` format.
    """
    if len(rom_bytes) > 0x200 and rom_bytes[0x100:0x104] == b"SEGA":
        return rom_bytes
    if (ext.lower() == ".smd" or len(rom_bytes) % 16384 == 512) and len(rom_bytes) > 512:
        body = rom_bytes[512:]
        if len(body) % 16384 == 0:
            out = bytearray(len(body))
            for blk_off in range(0, len(body), 16384):
                block = body[blk_off : blk_off + 16384]
                # Even bytes (1, 3, 5...) in first 8KB, odd bytes (0, 2, 4...) in second 8KB
                out[blk_off + 1 : blk_off + 16384 : 2] = block[:8192]
                out[blk_off + 0 : blk_off + 16384 : 2] = block[8192:16384]
            if out[0x100:0x104] == b"SEGA" or ext.lower() == ".smd":
                return bytes(out)
    return rom_bytes


def strip_snes_smc_header(rom_bytes: bytes) -> bytes:
    """Strip the 512-byte copier header from `.smc` SNES ROMs if present."""
    if len(rom_bytes) > 512 and (len(rom_bytes) % 1024) == 512:
        return rom_bytes[512:]
    return rom_bytes


def detect_console_system(
    file_path: str,
    rom_bytes: Optional[bytes] = None,
    force_system: Optional[str] = None,
) -> Tuple[str, str, bytes]:
    """
    Load `rom_bytes` from `file_path` (unpacking `.zip` if needed) and determine the console system key:
    `("gba" | "gb" | "gbc" | "nes" | "snes" | "md", effective_filename, raw_rom_bytes)`.
    """
    p = Path(file_path)
    effective_name = p.name
    if rom_bytes is None:
        rom_bytes = p.expanduser().resolve().read_bytes()

    # Check if input is a ZIP archive
    if p.suffix.lower() == ".zip" or rom_bytes[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(rom_bytes)) as zf:
            candidates = [
                info
                for info in zf.infolist()
                if not info.is_dir()
                and not info.filename.startswith("__MACOSX/")
                and not Path(info.filename).name.startswith(".")
                and Path(info.filename).suffix.lower() in ALL_ROM_EXTENSIONS
            ]
            if not candidates:
                raise ValueError(f"ZIP archive '{p.name}' does not contain any supported ROM ({ALL_ROM_EXTENSIONS})")
            chosen = max(candidates, key=lambda i: i.file_size)
            effective_name = Path(chosen.filename).name
            rom_bytes = zf.read(chosen)

    ext = Path(effective_name).suffix.lower()

    if force_system and force_system.lower() in SYSTEM_SPECS:
        sys_key = force_system.lower()
    elif ext in (".gba", ".agb"):
        sys_key = "gba"
    elif ext in (".gb", ".sgb"):
        # If the ROM header explicitly requires GBC (0xC0), classify as gbc
        if len(rom_bytes) > 0x143 and rom_bytes[0x143] == 0xC0:
            sys_key = "gbc"
        else:
            sys_key = "gb"
    elif ext == ".gbc":
        sys_key = "gbc"
    elif ext == ".nes" or rom_bytes[:4] in (b"NES\x1a", b"TNES"):
        sys_key = "nes"
    elif ext in (".sfc", ".smc"):
        sys_key = "snes"
    elif ext in (".md", ".gen", ".smd", ".bin"):
        sys_key = "md"
    elif ext in (".nds", ".srl", ".dsi", ".ids"):
        sys_key = "nds"
    else:
        # Magic-byte fallback
        if rom_bytes[:4] in (b"NES\x1a", b"TNES"):
            sys_key = "nes"
        elif len(rom_bytes) >= 0x160 and rom_bytes[0x0C0 : 0x0C0 + len(NINTENDO_LOGO_PREFIX)] == NINTENDO_LOGO_PREFIX:
            sys_key = "nds"
        elif len(rom_bytes) > 0x150 and rom_bytes[0x104 : 0x104 + len(GB_LOGO_PREFIX)] == GB_LOGO_PREFIX:
            sys_key = "gbc" if rom_bytes[0x143] in (0x80, 0xC0) else "gb"
        elif len(rom_bytes) > 0x200 and rom_bytes[0x100:0x104] == b"SEGA":
            sys_key = "md"
        else:
            sys_key = "gba"

    if sys_key == "snes":
        rom_bytes = strip_snes_smc_header(rom_bytes)
    elif sys_key == "md":
        rom_bytes = deinterleave_smd_if_needed(rom_bytes, ext)
    elif sys_key == "nes":
        rom_bytes = _unhack_ffe_mapper6_nes(rom_bytes)

    return sys_key, effective_name, rom_bytes


def _derive_code_from_title(title: str, crc32: int, prefix_char: str) -> str:
    clean = "".join(c for c in title.upper() if "A" <= c <= "Z" or "0" <= c <= "9")
    if len(clean) >= 3:
        return f"{prefix_char}{clean[:3]}"
    return f"{prefix_char}{crc32 & 0xFFF:03X}"


def parse_gb_rom(
    file_path: str,
    rom_bytes: bytes,
    system: str = "gb",
    custom_unique_id: Optional[int] = None,
) -> GBARomInfo:
    """Parse a Game Boy (`.gb`) or Game Boy Color (`.gbc`) ROM header (`0x0100..0x014F`)."""
    raw_size = len(rom_bytes)
    crc32 = zlib.crc32(rom_bytes) & 0xFFFFFFFF
    md5 = hashlib.md5(rom_bytes).hexdigest().upper()
    sha1 = hashlib.sha1(rom_bytes).hexdigest().upper()

    logo_valid = (
        len(rom_bytes) >= 0x134
        and rom_bytes[0x104 : 0x104 + len(GB_LOGO_PREFIX)] == GB_LOGO_PREFIX
    )

    cgb_flag = rom_bytes[0x143] if len(rom_bytes) > 0x143 else 0
    if cgb_flag in (0x80, 0xC0):
        raw_title = rom_bytes[0x134:0x13F]
        raw_code = rom_bytes[0x13F:0x143]
    else:
        raw_title = rom_bytes[0x134:0x143] if len(rom_bytes) >= 0x143 else b""
        raw_code = b""

    internal_title = (
        "".join(chr(b) if 32 <= b <= 126 else " " for b in raw_title.split(b"\x00", 1)[0]).strip()
        or Path(file_path).stem
    )
    game_code = "".join(chr(b) for b in raw_code if 48 <= b <= 90).strip()
    if len(game_code) != 4:
        game_code = _derive_code_from_title(internal_title, crc32, "C" if system == "gbc" else "D")

    old_lic = rom_bytes[0x14B] if len(rom_bytes) > 0x14B else 0
    if old_lic == 0x33 and len(rom_bytes) >= 0x146:
        maker_code = "".join(chr(b) if 32 <= b <= 126 else "0" for b in rom_bytes[0x144:0x146])
    else:
        maker_code = f"{old_lic:02X}"

    cart_type = rom_bytes[0x147] if len(rom_bytes) > 0x147 else 0
    cart_name, has_battery, has_rtc = GB_CARTRIDGE_TYPES.get(
        cart_type, (f"MBC_0x{cart_type:02X}", True, False)
    )
    ram_code = rom_bytes[0x149] if len(rom_bytes) > 0x149 else 0
    ram_bytes = 512 if cart_type in (0x05, 0x06) else GB_RAM_SIZES.get(ram_code, 8 * 1024)

    header_chk = rom_bytes[0x14D] if len(rom_bytes) > 0x14D else 0
    comp_chk = 0
    if len(rom_bytes) > 0x14D:
        for b in rom_bytes[0x134:0x14D]:
            comp_chk = (comp_chk - b - 1) & 0xFF
    header_valid = logo_valid and (header_chk == comp_chk)

    dest_code = rom_bytes[0x14A] if len(rom_bytes) > 0x14A else 1
    region = "Japan" if dest_code == 0x00 else "USA"
    version = rom_bytes[0x14C] if len(rom_bytes) > 0x14C else 0

    save_desc = f"{cart_name} ({ram_bytes // 1024} KB SRAM)" if ram_bytes >= 1024 else cart_name
    spec = SYSTEM_SPECS[system]
    title_id = generate_title_id(game_code, crc32, custom_unique_id=custom_unique_id)

    return GBARomInfo(
        file_path=file_path,
        raw_size=raw_size,
        padded_size=raw_size,
        crc32=crc32,
        md5=md5,
        sha1=sha1,
        internal_title=internal_title,
        game_code=game_code,
        maker_code=maker_code,
        version=version,
        header_checksum=header_chk,
        computed_checksum=comp_chk,
        header_valid=header_valid,
        logo_valid=logo_valid,
        sdk_save_strings=[cart_name],
        has_rtc=has_rtc,
        save_type_id=SaveTypeID.SRAM_256K if has_battery else SaveTypeID.NONE,
        save_type_name=f"Nintendo 3DS {spec['name']} VC ({save_desc})",
        save_data_size=0x00100000,  # 1 MB for 3DS GB/GBC VC
        title_id=title_id,
        region=region,
        system=system,
        system_name=str(spec["name"]),
        lookup_crc32=crc32,
        product_prefix=str(spec["product_prefix"]),
        core_version=0x00000002,
    )


def _unhack_ffe_mapper6_nes(rom_bytes: bytes) -> bytes:
    """
    If `rom_bytes` is a Front Fareast (`[hFFE]`) Super Magic Card Mapper 6 hack of a 128KB PRG + 128KB CHR
    MMC1 (`Mapper 1`) game (with a 512-byte trainer at $7000..$71FF), restore its standard MMC1 register
    write subroutines and iNES Mapper 1 header (`PRG=8x16KB, CHR=16x8KB`) so it runs natively on 3DS NES VC.
    """
    if len(rom_bytes) != 16 + 512 + 262144 or rom_bytes[:4] != b"NES\x1a":
        return rom_bytes
    flags6, flags7 = rom_bytes[6], rom_bytes[7]
    mapper = (flags6 >> 4) | (flags7 & 0xF0)
    if mapper != 6 or not (flags6 & 0x04) or rom_bytes[4] != 16 or rom_bytes[5] != 0:
        return rom_bytes

    prg = bytearray(rom_bytes[16 + 512 : 16 + 512 + 131072])
    chr_rom = rom_bytes[16 + 512 + 131072 :]

    def _mmc1_write20(addr_hi: int) -> bytes:
        return bytes([0x8D, 0x00, addr_hi, 0x4A]) * 5

    if prg[130721:130724] == b"\x20\x09\x70" and prg[130759:130762] == b"\x20\x0c\x70":
        for cpu in (0xB8CB, 0xB902, 0xBDA8, 0xBDC5):
            off = 16384 + (cpu - 0x8000)
            if prg[off : off + 3] == b"\x20\xb0\xfe":
                prg[off : off + 3] = b"\x8d\x01\x20"
        off_bd6a = 16384 + (0xBD6A - 0x8000)
        off_bd81 = 16384 + (0xBD81 - 0x8000)
        prg[off_bd6a : off_bd6a + 23] = _mmc1_write20(0xC0) + b"\x4c\x06\xbf"
        prg[off_bd81 : off_bd81 + 23] = _mmc1_write20(0xA0) + b"\x4c\x06\xbf"
        for off in (119306, 119315, 129642):
            if prg[off : off + 3] == b"\x20\x15\x70":
                prg[off : off + 3] = b"\x20\xe2\xfe"
        if prg[129657:129660] == b"\x20\x12\x70":
            prg[129657:129660] = b"\x20\xbc\xfe"
        tail = b"\x68\x85\xff\x8d\x00\x20\x60"
        prg[130721 : 130721 + 27] = _mmc1_write20(0x80) + tail
        prg[130759 : 130759 + 27] = _mmc1_write20(0xA0) + tail
        prg[130797 : 130797 + 27] = _mmc1_write20(0xC0) + tail
        prg[130847 : 130847 + 27] = _mmc1_write20(0xE0) + tail
        if prg[131048:131051] == b"\x2c\xf1\xff":
            prg[131048:131051] = b"\xee\xeb\xff"

    new_hdr = bytearray(16)
    new_hdr[0:4] = b"NES\x1a"
    new_hdr[4] = 8
    new_hdr[5] = 16
    new_hdr[6] = 0x10 | (flags6 & 0x03)
    new_hdr[7] = 0x00
    return bytes(new_hdr) + bytes(prg) + chr_rom


def convert_ines_to_tnes(rom_bytes: bytes) -> bytes:
    """
    Convert a standard 16-byte iNES (`NES\\x1a`) ROM into Nintendo 3DS Virtual Console's
    `TNES` format (`16-byte TNES header + PRG + CHR`) placed at `RomFS:/rom/rom.bin`.
    """
    if rom_bytes[:4] == b"TNES":
        return rom_bytes
    if len(rom_bytes) < 16 or rom_bytes[:4] != b"NES\x1a":
        raise ValueError("Invalid NES ROM header (expected 'NES\\x1a' or 'TNES')")

    rom_bytes = _unhack_ffe_mapper6_nes(rom_bytes)

    prg_16k = rom_bytes[4]
    chr_8k = rom_bytes[5]
    flags6 = rom_bytes[6]
    flags7 = rom_bytes[7]
    ines_mapper = (flags6 >> 4) | (flags7 & 0xF0)
    has_battery = bool(flags6 & 0x02)
    has_trainer = bool(flags6 & 0x04)
    vert_mirror = bool(flags6 & 0x01)

    tnes_mapper = INES_TO_TNES_MAPPER.get(ines_mapper, 0)
    prg_8k = prg_16k * 2
    wram = 1 if (has_battery or ines_mapper in (1, 4, 5, 9, 10, 19, 69, 85)) else 0
    if ines_mapper in (1, 4, 5, 7, 9, 10, 16, 18, 19, 69, 85):
        mirror = 0  # Mapper-controlled mirroring
    else:
        mirror = 2 if vert_mirror else 1
    battery = 1 if has_battery else 0

    tnes_hdr = bytearray(16)
    tnes_hdr[0:4] = b"TNES"
    tnes_hdr[4] = tnes_mapper & 0xFF
    tnes_hdr[5] = prg_8k & 0xFF
    tnes_hdr[6] = chr_8k & 0xFF
    tnes_hdr[7] = wram
    tnes_hdr[8] = mirror
    tnes_hdr[9] = battery

    payload_off = 16 + (512 if has_trainer else 0)
    return bytes(tnes_hdr) + rom_bytes[payload_off:]


def parse_nes_rom(
    file_path: str,
    rom_bytes: bytes,
    custom_unique_id: Optional[int] = None,
) -> GBARomInfo:
    """Parse an iNES / NES 2.0 (`.nes`) or `TNES` ROM header."""
    raw_size = len(rom_bytes)
    crc32 = zlib.crc32(rom_bytes) & 0xFFFFFFFF
    md5 = hashlib.md5(rom_bytes).hexdigest().upper()
    sha1 = hashlib.sha1(rom_bytes).hexdigest().upper()

    header_valid = len(rom_bytes) >= 16 and rom_bytes[:4] in (b"NES\x1a", b"TNES")
    if len(rom_bytes) >= 16 and rom_bytes[:4] == b"NES\x1a":
        prg_16k = rom_bytes[4]
        chr_8k = rom_bytes[5]
        flags6 = rom_bytes[6]
        flags7 = rom_bytes[7]
        mapper = (flags6 >> 4) | (flags7 & 0xF0)
        has_battery = bool(flags6 & 0x02)
        has_trainer = bool(flags6 & 0x04)
        payload = rom_bytes[16 + (512 if has_trainer else 0) :]
        lookup_crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    else:
        prg_16k = (rom_bytes[5] // 2) if len(rom_bytes) >= 16 else 0
        chr_8k = rom_bytes[6] if len(rom_bytes) >= 16 else 0
        mapper = rom_bytes[4] if len(rom_bytes) >= 16 else 0
        has_battery = bool(rom_bytes[9]) if len(rom_bytes) >= 16 else False
        lookup_crc32 = zlib.crc32(rom_bytes[16:]) & 0xFFFFFFFF if len(rom_bytes) > 16 else crc32

    stem_raw = Path(file_path).stem
    stem = stem_raw.split(" (")[0].split(" [")[0].strip()
    internal_title = stem[:21].upper() or "NES GAME"
    game_code = _derive_code_from_title(internal_title, lookup_crc32, "N")
    title_id = generate_title_id(game_code, lookup_crc32, custom_unique_id=custom_unique_id)
    spec = SYSTEM_SPECS["nes"]
    if "(J)" in stem_raw or "(Japan)" in stem_raw:
        region = "Japan"
    elif "(E)" in stem_raw or "(Europe)" in stem_raw:
        region = "Europe"
    else:
        region = "USA"

    return GBARomInfo(
        file_path=file_path,
        raw_size=raw_size,
        padded_size=raw_size,
        crc32=crc32,
        md5=md5,
        sha1=sha1,
        internal_title=internal_title,
        game_code=game_code,
        maker_code="01",
        version=0,
        header_checksum=mapper & 0xFF,
        computed_checksum=mapper & 0xFF,
        header_valid=header_valid,
        logo_valid=header_valid,
        sdk_save_strings=[f"iNES_MAPPER_{mapper}", f"PRG_{prg_16k*16}KB_CHR_{chr_8k*8}KB"],
        has_rtc=False,
        save_type_id=SaveTypeID.SRAM_256K if has_battery else SaveTypeID.NONE,
        save_type_name=f"Nintendo 3DS NES VC (Mapper {mapper}{', Battery SRAM' if has_battery else ''})",
        save_data_size=0x00100000,  # 1 MB for 3DS NES VC
        title_id=title_id,
        region=region,
        system="nes",
        system_name=str(spec["name"]),
        lookup_crc32=lookup_crc32,
        product_prefix=str(spec["product_prefix"]),
        core_version=0x00000002,
    )


def _detect_snes_header_offset(rom_bytes: bytes) -> Tuple[int, bool]:
    """Return `(header_offset, is_hirom)` for a headerless `.sfc` SNES ROM."""
    best_off = 0x7FC0
    best_score = -1
    best_hirom = False

    for cand, is_hi in ((0x7FC0, False), (0xFFC0, True), (0x40FFC0, True)):
        if len(rom_bytes) < cand + 0x20:
            continue
        score = 0
        comp, chk = struct.unpack_from("<HH", rom_bytes, cand + 0x1C)
        if (comp ^ chk) == 0xFFFF and chk != 0:
            score += 5
        map_mode = rom_bytes[cand + 0x15]
        if not is_hi and (map_mode & 0x0F) in (0x00, 0x02, 0x03):
            score += 2
        if is_hi and (map_mode & 0x0F) in (0x01, 0x05):
            score += 2
        title_slice = rom_bytes[cand : cand + 21]
        printable = sum(1 for b in title_slice if 32 <= b <= 126)
        score += printable // 4
        if score > best_score:
            best_score = score
            best_off = cand
            best_hirom = is_hi

    return best_off, best_hirom


def build_snes_vc_data_bin(
    rom_bytes: bytes,
    header_template: bytes,
    is_hirom: bool = False,
) -> bytes:
    """
    Construct the Nintendo 3DS SNES Virtual Console `RomFS:/data.bin` container:
      - `0x00..0x60`: 96-byte `.ves` header with exact ROM size & LoROM (`0x1464`) / HiROM (`0x1564`) preset ID
      - `0x60..`: Headerless `.sfc` ROM padded to 64 KB alignment
    """
    clean_rom = strip_snes_smc_header(rom_bytes)
    rem = len(clean_rom) % 0x10000
    if rem != 0:
        clean_rom = clean_rom + b"\xFF" * (0x10000 - rem)

    rom_len = len(clean_rom)
    total_len = 0x60 + rom_len

    hdr = bytearray(header_template[:0x60] if len(header_template) >= 0x60 else b"\x00" * 0x60)
    struct.pack_into("<I", hdr, 0x00, 0x00000100)
    struct.pack_into("<I", hdr, 0x04, total_len)
    struct.pack_into("<I", hdr, 0x08, 0x00000030)
    struct.pack_into("<I", hdr, 0x0C, 0x00000050)
    struct.pack_into("<I", hdr, 0x10, 0x00000060)
    struct.pack_into("<I", hdr, 0x14, total_len)
    struct.pack_into("<I", hdr, 0x18, total_len)
    struct.pack_into("<I", hdr, 0x1C, 0x00000000)
    struct.pack_into("<I", hdr, 0x20, total_len)
    hdr[0x24:0x30] = b"KTR-UAEE\x00\x00\x00\x00"
    hdr[0x30] = 0x3C
    struct.pack_into("<I", hdr, 0x31, rom_len)
    struct.pack_into("<H", hdr, 0x40, 0x1564 if is_hirom else 0x1464)
    struct.pack_into("<II", hdr, 0x50, 3, 1)
    return bytes(hdr) + clean_rom


def parse_snes_rom(
    file_path: str,
    rom_bytes: bytes,
    custom_unique_id: Optional[int] = None,
) -> GBARomInfo:
    """Parse a Super Nintendo (`.sfc` / `.smc`) ROM header."""
    clean_rom = strip_snes_smc_header(rom_bytes)
    raw_size = len(clean_rom)
    crc32 = zlib.crc32(clean_rom) & 0xFFFFFFFF
    md5 = hashlib.md5(clean_rom).hexdigest().upper()
    sha1 = hashlib.sha1(clean_rom).hexdigest().upper()

    hdr_off, is_hirom = _detect_snes_header_offset(clean_rom)
    if len(clean_rom) >= hdr_off + 0x20:
        raw_title = clean_rom[hdr_off : hdr_off + 21]
        internal_title = (
            "".join(chr(b) if 32 <= b <= 126 else " " for b in raw_title).strip()
            or Path(file_path).stem
        )
        sram_exp = clean_rom[hdr_off + 0x18]
        sram_kb = (1 << sram_exp) if 0 < sram_exp <= 8 else 0
        country = clean_rom[hdr_off + 0x19]
        dev_id = clean_rom[hdr_off + 0x1A]
        version = clean_rom[hdr_off + 0x1B]
        comp, chk = struct.unpack_from("<HH", clean_rom, hdr_off + 0x1C)
        header_valid = (comp ^ chk) == 0xFFFF
        if dev_id == 0x33 and hdr_off >= 0x10:
            raw_code = clean_rom[hdr_off - 0x0E : hdr_off - 0x0A]
            game_code = "".join(chr(b) for b in raw_code if 48 <= b <= 90).strip()
            raw_maker = clean_rom[hdr_off - 0x10 : hdr_off - 0x0E]
            maker_code = "".join(chr(b) if 32 <= b <= 126 else "0" for b in raw_maker)
        else:
            game_code = ""
            maker_code = f"{dev_id:02X}"
    else:
        internal_title = Path(file_path).stem[:21].upper()
        sram_kb = 0
        country = 1
        version = 0
        chk = 0
        header_valid = False
        game_code = ""
        maker_code = "01"

    if len(game_code) != 4:
        game_code = _derive_code_from_title(internal_title, crc32, "S")

    region = "Japan" if country == 0x00 else ("USA" if country == 0x01 else "Europe")
    map_label = "HiROM" if is_hirom else "LoROM"
    title_id = generate_title_id(game_code, crc32, custom_unique_id=custom_unique_id)
    spec = SYSTEM_SPECS["snes"]

    return GBARomInfo(
        file_path=file_path,
        raw_size=raw_size,
        padded_size=raw_size,
        crc32=crc32,
        md5=md5,
        sha1=sha1,
        internal_title=internal_title,
        game_code=game_code,
        maker_code=maker_code,
        version=version,
        header_checksum=chk & 0xFF,
        computed_checksum=chk & 0xFF,
        header_valid=header_valid,
        logo_valid=header_valid,
        sdk_save_strings=[map_label, f"SRAM_{sram_kb}KB"],
        has_rtc=False,
        save_type_id=SaveTypeID.SRAM_256K if sram_kb > 0 else SaveTypeID.NONE,
        save_type_name=f"Nintendo 3DS SNES VC ({map_label}, {sram_kb} KB SRAM)",
        save_data_size=0x00300000,  # 3 MB for 3DS SNES VC
        title_id=title_id,
        region=region,
        system="snes",
        system_name=str(spec["name"]),
        lookup_crc32=crc32,
        product_prefix=str(spec["product_prefix"]),
        core_version=0x00000002,
    )


def parse_md_rom(
    file_path: str,
    rom_bytes: bytes,
    custom_unique_id: Optional[int] = None,
) -> GBARomInfo:
    """Parse a Sega Genesis / Mega Drive (`.md`, `.gen`, `.smd`, `.bin`) ROM header (`0x0100..0x01FF`)."""
    clean_rom = deinterleave_smd_if_needed(rom_bytes, Path(file_path).suffix)
    raw_size = len(clean_rom)
    crc32 = zlib.crc32(clean_rom) & 0xFFFFFFFF
    md5 = hashlib.md5(clean_rom).hexdigest().upper()
    sha1 = hashlib.sha1(clean_rom).hexdigest().upper()

    header_valid = len(clean_rom) >= 0x200 and clean_rom[0x100:0x104] == b"SEGA"
    if len(clean_rom) >= 0x200:
        overseas = "".join(chr(b) if 32 <= b <= 126 else " " for b in clean_rom[0x150:0x180]).strip()
        domestic = "".join(chr(b) if 32 <= b <= 126 else " " for b in clean_rom[0x120:0x150]).strip()
        internal_title = overseas or domestic or Path(file_path).stem
        serial_str = "".join(chr(b) if 32 <= b <= 126 else " " for b in clean_rom[0x180:0x18E]).strip()
        (chk,) = struct.unpack_from(">H", clean_rom, 0x18E)
        has_sram = clean_rom[0x1B0:0x1B2] == b"RA"
        reg_str = "".join(chr(b) if 32 <= b <= 126 else "" for b in clean_rom[0x1F0:0x1F3]).strip().upper()
    else:
        internal_title = Path(file_path).stem[:32].upper()
        serial_str = ""
        chk = 0
        has_sram = False
        reg_str = "U"

    alnum_serial = "".join(c for c in serial_str.upper() if c.isalnum())
    game_code = alnum_serial[-4:] if len(alnum_serial) >= 4 else _derive_code_from_title(internal_title, crc32, "M")
    region = "USA" if "U" in reg_str else ("Japan" if "J" in reg_str else ("Europe" if "E" in reg_str else "World"))
    title_id = generate_title_id(game_code, crc32, custom_unique_id=custom_unique_id)
    spec = SYSTEM_SPECS["md"]

    return GBARomInfo(
        file_path=file_path,
        raw_size=raw_size,
        padded_size=raw_size,
        crc32=crc32,
        md5=md5,
        sha1=sha1,
        internal_title=internal_title,
        game_code=game_code,
        maker_code="SE",
        version=0,
        header_checksum=chk & 0xFF,
        computed_checksum=chk & 0xFF,
        header_valid=header_valid,
        logo_valid=header_valid,
        sdk_save_strings=["SRAM_64KB"] if has_sram else ["NO_SRAM"],
        has_rtc=False,
        save_type_id=SaveTypeID.SRAM_256K if has_sram else SaveTypeID.NONE,
        save_type_name=f"PicoDrive 3DS Forwarder ({'SRAM Enabled' if has_sram else 'Standard'})",
        save_data_size=0x00000000,  # PicoDrive forwarder stores saves on SD card
        title_id=title_id,
        region=region,
        system="md",
        system_name=str(spec["name"]),
        lookup_crc32=crc32,
        product_prefix=str(spec["product_prefix"]),
        core_version=0x00000002,
    )


def crc16_nds(data: bytes) -> int:
    """Compute the standard Nintendo DS CRC16-MODBUS checksum (`init=0xFFFF`, `poly=0xA001`)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def extract_nds_banner_titles(rom_bytes: bytes) -> Dict[str, List[str]]:
    """
    Extract UTF-16LE multi-language title lines from a Nintendo DS ROM's embedded Icon/Title Banner:
      `0x240`: Japanese (`jpn`), `0x340`: English (`eng`), `0x440`: French (`fra`),
      `0x540`: German (`ger`), `0x640`: Italian (`ita`), `0x740`: Spanish (`spa`),
      `0x840`: Chinese (`chn`, version >= 2), `0x940`: Korean (`kor`, version >= 3).
    """
    if len(rom_bytes) < 0x06C:
        return {}
    (banner_off,) = struct.unpack_from("<I", rom_bytes, 0x068)
    if banner_off < 0x200 or len(rom_bytes) < banner_off + 0x840:
        return {}

    (ver,) = struct.unpack_from("<H", rom_bytes, banner_off)
    ver_low = ver & 0xFF
    if ver_low not in (0x01, 0x02, 0x03, 0x103):
        return {}

    langs = ["jpn", "eng", "fra", "ger", "ita", "spa"]
    if ver_low >= 2 and len(rom_bytes) >= banner_off + 0x940:
        langs.append("chn")
    if ver_low >= 3 and len(rom_bytes) >= banner_off + 0xA40:
        langs.append("kor")

    out: Dict[str, List[str]] = {}
    for idx, lang in enumerate(langs):
        slot_off = banner_off + 0x240 + idx * 0x100
        raw_slot = rom_bytes[slot_off : slot_off + 0x100]
        text = raw_slot.decode("utf-16le", errors="ignore").split("\x00", 1)[0].strip()
        if text:
            lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n") if ln.strip()]
            if lines:
                out[lang] = lines
    return out


def extract_nds_banner_icon(rom_bytes: bytes) -> Optional[RGBAImage]:
    """
    Decode the 32x32 4bpp tiled cartridge icon and 16-color BGR555 palette from a Nintendo DS ROM's
    embedded Icon/Title Banner (`banner_offset + 0x020..0x240`).
    Returns a 32x32 `RGBAImage`, or `None` if no valid banner icon is present.
    """
    from .graphics import RGBAImage

    if len(rom_bytes) < 0x06C:
        return None
    (banner_off,) = struct.unpack_from("<I", rom_bytes, 0x068)
    if banner_off < 0x200 or len(rom_bytes) < banner_off + 0x240:
        return None

    tile_bytes = rom_bytes[banner_off + 0x020 : banner_off + 0x220]
    pal_words = struct.unpack_from("<16H", rom_bytes, banner_off + 0x220)
    if not any(tile_bytes) and not any(pal_words):
        return None

    palette: List[RGBA] = []
    for idx, c in enumerate(pal_words):
        r = ((c & 0x1F) * 255) // 31
        g = (((c >> 5) & 0x1F) * 255) // 31
        b = (((c >> 10) & 0x1F) * 255) // 31
        palette.append((255, 255, 255, 255) if idx == 0 else (r, g, b, 255))

    pixels: List[RGBA] = [(255, 255, 255, 255)] * (32 * 32)
    pos = 0
    for ty in range(4):
        for tx in range(4):
            for py in range(8):
                y = ty * 8 + py
                for px in range(0, 8, 2):
                    b_val = tile_bytes[pos]
                    pos += 1
                    x = tx * 8 + px
                    pixels[y * 32 + x] = palette[b_val & 0x0F]
                    pixels[y * 32 + x + 1] = palette[(b_val >> 4) & 0x0F]

    return RGBAImage(32, 32, pixels)


def parse_nds_rom(
    file_path: str,
    rom_bytes: bytes,
    custom_unique_id: Optional[int] = None,
) -> GBARomInfo:
    """Parse a Nintendo DS (`.nds`, `.srl`, `.dsi`, `.ids`) ROM header (`0x000..0x200`) and embedded banner."""
    raw_size = len(rom_bytes)
    crc32 = zlib.crc32(rom_bytes) & 0xFFFFFFFF
    md5 = hashlib.md5(rom_bytes).hexdigest().upper()
    sha1 = hashlib.sha1(rom_bytes).hexdigest().upper()

    if len(rom_bytes) >= 0x160:
        raw_title = rom_bytes[0x000:0x00C]
        internal_title = (
            "".join(chr(b) if 32 <= b <= 126 else " " for b in raw_title.split(b"\x00", 1)[0]).strip()
            or Path(file_path).stem[:12].upper()
        )
        raw_code = rom_bytes[0x00C:0x010]
        game_code = "".join(chr(b) for b in raw_code if 48 <= b <= 90).strip()
        raw_maker = rom_bytes[0x010:0x012]
        maker_code = "".join(chr(b) if 32 <= b <= 126 else "0" for b in raw_maker).strip() or "01"
        unit_code = rom_bytes[0x012]
        nds_region_byte = rom_bytes[0x01D]
        version = rom_bytes[0x01E]
        (arm9_size,) = struct.unpack_from("<I", rom_bytes, 0x02C)
        (arm7_size,) = struct.unpack_from("<I", rom_bytes, 0x03C)
        (banner_off,) = struct.unpack_from("<I", rom_bytes, 0x068)
        logo_crc, hdr_crc = struct.unpack_from("<HH", rom_bytes, 0x15C)
        comp_hdr_crc = crc16_nds(rom_bytes[0x000:0x15E])
        logo_valid = (rom_bytes[0x0C0:0x0D0] == NINTENDO_LOGO_PREFIX) and (logo_crc == 0xCF56)
        header_valid = logo_valid and (hdr_crc == comp_hdr_crc)
    else:
        internal_title = Path(file_path).stem[:12].upper()
        game_code = ""
        maker_code = "01"
        unit_code = 0
        nds_region_byte = 0
        version = 0
        arm9_size = 0
        arm7_size = 0
        banner_off = 0
        hdr_crc = 0
        comp_hdr_crc = 0
        logo_valid = False
        header_valid = False

    if len(game_code) != 4:
        game_code = _derive_code_from_title(internal_title, crc32, "N")

    if nds_region_byte == 0x80:
        region = "China"
    elif nds_region_byte == 0x40:
        region = "Korea"
    else:
        reg_char = game_code[3:4].upper() if len(game_code) >= 4 else "E"
        region = REGION_MAP.get(reg_char, "USA")

    unit_tag = "DSIWARE" if unit_code == 0x03 else ("TWL_ENHANCED" if unit_code == 0x02 else "NTR_ROM")
    unit_desc = (
        "DSiWare / DSi Exclusive"
        if unit_code == 0x03
        else ("DSi-Enhanced (TWL)" if unit_code == 0x02 else "Nintendo DS (NTR)")
    )
    sdk_features = [unit_tag, f"ARM9_{arm9_size // 1024}KB", f"ARM7_{arm7_size // 1024}KB"]
    if banner_off >= 0x200 and len(rom_bytes) >= banner_off + 0x840:
        sdk_features.append("BANNER_ICON")

    banner_titles = extract_nds_banner_titles(rom_bytes)
    chosen_lines = (
        banner_titles.get("eng")
        or banner_titles.get("chn")
        or banner_titles.get("jpn")
        or next(iter(banner_titles.values()), [])
    )
    banner_title = ""
    banner_publisher = ""
    if len(chosen_lines) >= 3:
        banner_title = f"{chosen_lines[0]}: {chosen_lines[1]}"
        banner_publisher = chosen_lines[-1]
    elif len(chosen_lines) == 2:
        banner_title = chosen_lines[0]
        banner_publisher = chosen_lines[1]
    elif len(chosen_lines) == 1:
        banner_title = chosen_lines[0]

    title_id = generate_title_id(game_code, crc32, custom_unique_id=custom_unique_id)
    spec = SYSTEM_SPECS["nds"]

    return GBARomInfo(
        file_path=file_path,
        raw_size=raw_size,
        padded_size=raw_size,
        crc32=crc32,
        md5=md5,
        sha1=sha1,
        internal_title=internal_title,
        game_code=game_code,
        maker_code=maker_code,
        version=version,
        header_checksum=hdr_crc & 0xFFFF,
        computed_checksum=comp_hdr_crc & 0xFFFF,
        header_valid=header_valid,
        logo_valid=logo_valid,
        sdk_save_strings=sdk_features,
        has_rtc=True,
        save_type_id=SaveTypeID.FLASH_512K_PANASONIC,
        save_type_name=f"nds-bootstrap 3DS Forwarder ({unit_desc}, SD Card Save)",
        save_data_size=0x00000000,
        title_id=title_id,
        region=region,
        system="nds",
        system_name=str(spec["name"]),
        lookup_crc32=crc32,
        product_prefix=str(spec["product_prefix"]),
        core_version=0x00000002,
        banner_title=banner_title,
        banner_publisher=banner_publisher,
    )


def parse_rom_auto(
    file_path: str,
    rom_bytes: Optional[bytes] = None,
    override_save_type: Optional[str] = None,
    override_rtc: Optional[bool] = None,
    custom_unique_id: Optional[int] = None,
    force_system: Optional[str] = None,
) -> Tuple[GBARomInfo, bytes]:
    """
    Auto-detect the console system (`.gba`, `.gb`, `.gbc`, `.nes`, `.sfc`/`.smc`, `.md`/`.gen`, `.nds`, or `.zip`),
    parse its ROM header, and return `(rom_info, normalized_rom_bytes)`.
    """
    sys_key, effective_name, clean_bytes = detect_console_system(
        file_path, rom_bytes=rom_bytes, force_system=force_system
    )
    if sys_key == "gba":
        info = parse_gba_rom(
            file_path=effective_name,
            rom_bytes=clean_bytes,
            override_save_type=override_save_type,
            override_rtc=override_rtc,
            custom_unique_id=custom_unique_id,
        )
        return info, clean_bytes
    if sys_key in ("gb", "gbc"):
        return (
            parse_gb_rom(
                file_path=effective_name,
                rom_bytes=clean_bytes,
                system=sys_key,
                custom_unique_id=custom_unique_id,
            ),
            clean_bytes,
        )
    if sys_key == "nes":
        return (
            parse_nes_rom(
                file_path=effective_name,
                rom_bytes=clean_bytes,
                custom_unique_id=custom_unique_id,
            ),
            clean_bytes,
        )
    if sys_key == "snes":
        return (
            parse_snes_rom(
                file_path=effective_name,
                rom_bytes=clean_bytes,
                custom_unique_id=custom_unique_id,
            ),
            clean_bytes,
        )
    if sys_key == "md":
        return (
            parse_md_rom(
                file_path=effective_name,
                rom_bytes=clean_bytes,
                custom_unique_id=custom_unique_id,
            ),
            clean_bytes,
        )
    if sys_key == "nds":
        return (
            parse_nds_rom(
                file_path=effective_name,
                rom_bytes=clean_bytes,
                custom_unique_id=custom_unique_id,
            ),
            clean_bytes,
        )
    raise ValueError(f"Unsupported console system: {sys_key}")
