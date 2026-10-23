"""
GBA ROM Header Parser, Save Type & RTC Detector, IPS Patcher, and TitleID Generator.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
import struct
import zlib
from typing import Dict, List, Optional, Tuple

# Maximum GBA ROM size supported by AGB_FIRM (32 MB = 256 Mbit)
MAX_GBA_ROM_SIZE = 32 * 1024 * 1024
MIN_GBA_ROM_SIZE = 0xC0

# Nintendo GBA Logo bytes at 0x04..0x9F (first 16 bytes for quick check)
NINTENDO_LOGO_PREFIX = bytes.fromhex("24FFAE51699AA2213D84820A84E409AD")

# Regex patterns for Nintendo GBA SDK save library identifiers
SAVE_STRING_REGEX = re.compile(
    rb"(EEPROM_V\d{3}|SRAM_V\d{3}|SRAM_F_V\d{3}|FLASH_V\d{3}|FLASH512_V\d{3}|FLASH1M_V\d{3})"
)
RTC_STRING_REGEX = re.compile(rb"SIIRTC_V\d{3}")


class SaveTypeID:
    """
    AGB_FIRM (.CAA Config Block Offset 0x08) Hardware Save Emulation IDs.
    Verified against AGB_FIRM disassembly, gbacia.c, and vcinject/vcoven.
    """

    EEPROM_4K = 0x00               # 512 B (4kbit) EEPROM (ROM <= 16 MB)
    EEPROM_4K_256M = 0x01          # 512 B (4kbit) EEPROM (ROM > 16 MB)
    EEPROM_64K = 0x02              # 8 KB (64kbit) EEPROM (ROM <= 16 MB)
    EEPROM_64K_256M = 0x03         # 8 KB (64kbit) EEPROM (ROM > 16 MB)
    FLASH_512K_ATMEL_RTC = 0x04    # 64 KB (512kbit) Flash (Atmel) + RTC
    FLASH_512K_ATMEL = 0x05        # 64 KB (512kbit) Flash (Atmel)
    FLASH_512K_SST_RTC = 0x06      # 64 KB (512kbit) Flash (SST) + RTC
    FLASH_512K_SST = 0x07          # 64 KB (512kbit) Flash (SST)
    FLASH_512K_PANASONIC_RTC = 0x08  # 64 KB (512kbit) Flash (Panasonic) + RTC
    FLASH_512K_PANASONIC = 0x09    # 64 KB (512kbit) Flash (Panasonic, standard default)
    FLASH_1M_MACRONIX_RTC = 0x0A   # 128 KB (1Mbit) Flash (Macronix 0x09C2) + RTC (Pokemon R/S/E)
    FLASH_1M_MACRONIX = 0x0B       # 128 KB (1Mbit) Flash (Macronix 0x09C2, Pokemon FR/LG, SMA4)
    FLASH_1M_SANYO_RTC = 0x0C      # 128 KB (1Mbit) Flash (Sanyo 0x1362) + RTC
    FLASH_1M_SANYO = 0x0D          # 128 KB (1Mbit) Flash (Sanyo 0x1362)
    SRAM_256K = 0x0E               # 32 KB (256kbit) SRAM/FRAM
    NONE = 0x0F                    # No save hardware

    # Backwards-compatibility aliases
    FLASH_512K_MACRONIX = FLASH_512K_PANASONIC
    FLASH_512K_MACRONIX_RTC = FLASH_512K_PANASONIC_RTC
    SRAM_256K_RTC = SRAM_256K
    SRAM_256K_ALT = SRAM_256K


SAVE_TYPE_NAMES: Dict[int, str] = {
    SaveTypeID.EEPROM_4K: "EEPROM 4kbit (512 B)",
    SaveTypeID.EEPROM_4K_256M: "EEPROM 4kbit (512 B, 256Mbit ROM bus)",
    SaveTypeID.EEPROM_64K: "EEPROM 64kbit (8 KB)",
    SaveTypeID.EEPROM_64K_256M: "EEPROM 64kbit (8 KB, 256Mbit ROM bus)",
    SaveTypeID.FLASH_512K_ATMEL_RTC: "Flash 512kbit (64 KB, Atmel) + RTC",
    SaveTypeID.FLASH_512K_ATMEL: "Flash 512kbit (64 KB, Atmel)",
    SaveTypeID.FLASH_512K_SST_RTC: "Flash 512kbit (64 KB, SST) + RTC",
    SaveTypeID.FLASH_512K_SST: "Flash 512kbit (64 KB, SST)",
    SaveTypeID.FLASH_512K_PANASONIC_RTC: "Flash 512kbit (64 KB, Panasonic) + RTC",
    SaveTypeID.FLASH_512K_PANASONIC: "Flash 512kbit (64 KB, Panasonic)",
    SaveTypeID.FLASH_1M_MACRONIX_RTC: "Flash 1Mbit (128 KB, Macronix) + RTC",
    SaveTypeID.FLASH_1M_MACRONIX: "Flash 1Mbit (128 KB, Macronix)",
    SaveTypeID.FLASH_1M_SANYO_RTC: "Flash 1Mbit (128 KB, Sanyo) + RTC",
    SaveTypeID.FLASH_1M_SANYO: "Flash 1Mbit (128 KB, Sanyo)",
    SaveTypeID.SRAM_256K: "SRAM 256kbit (32 KB)",
    SaveTypeID.NONE: "None (No Save)",
}

# Known 3-character Game Code prefixes (rom[0xAC:0xAF]) that strictly use 4kbit (512 B) EEPROM.
# Note: Most commercial EEPROM games use 64kbit (8 KB) or work with 64kbit, whereas Classic NES
# Series ("Fxx") and early 4kbit titles below require 4kbit EEPROM (0x00/0x01).
EEPROM_4K_PREFIXES = {
    "AMA",  # Super Mario Advance
    "A7K",  # Kirby: Nightmare in Dream Land
    "AWZ",  # WarioWare, Inc. (uses SRAM, but if EEPROM patched)
    "A2W",  # Game & Watch Gallery 4
    "AKW",  # Kuru Kuru Kururin
    "ATC",  # ChuChu Rocket!
    "A5F",  # F-Zero: Maximum Velocity (SRAM, fallback)
    "ARX",  # Rayman Advance
    "AST",  # Super Street Fighter II Turbo Revival
    "ABE",  # Bomberman Tournament
    "ABM",  # Bomberman Max 2
    "AFN",  # Earthworm Jim
    "A3P",  # Pac-Man Collection
    "A4P",  # Ms. Pac-Man Maze Madness
    "A8S",  # Spyro: Season of Ice
    "A2S",  # Spyro 2: Season of Flame
    "ATJ",  # Tom and Jerry: The Magic Ring
}

# Known 3-character Game Code prefixes with Real-Time Clock (Seiko S-3511A RTC)
KNOWN_RTC_PREFIXES = {
    "AXV",  # Pokemon Ruby
    "AXP",  # Pokemon Sapphire
    "BPE",  # Pokemon Emerald
    "U3I",  # Boktai: The Sun Is in Your Eyes
    "U32",  # Boktai 2: Solar Boy Django
    "U33",  # Shin Bokura no Taiyou: Gyakushuu no Sabata
    "BKA",  # Sennen Kazoku
    "BR4",  # RockMan EXE 4.5 Real Operation
}

# Known explicit Game Code prefix overrides -> SaveTypeID (for games with unusual chip checks)
EXPLICIT_SAVE_OVERRIDES: Dict[str, int] = {
    "AWR": SaveTypeID.FLASH_512K_MACRONIX,      # Advance Wars (Flash 512K)
    "B2W": SaveTypeID.FLASH_512K_MACRONIX,      # Advance Wars 2: Black Hole Rising (Flash 512K)
    "AXV": SaveTypeID.FLASH_1M_MACRONIX_RTC,    # Pokemon Ruby (Flash 1M + RTC)
    "AXP": SaveTypeID.FLASH_1M_MACRONIX_RTC,    # Pokemon Sapphire (Flash 1M + RTC)
    "BPE": SaveTypeID.FLASH_1M_MACRONIX_RTC,    # Pokemon Emerald (Flash 1M + RTC)
    "BPR": SaveTypeID.FLASH_1M_MACRONIX,        # Pokemon FireRed (Flash 1M)
    "BPG": SaveTypeID.FLASH_1M_MACRONIX,        # Pokemon LeafGreen (Flash 1M)
    "AX4": SaveTypeID.FLASH_1M_MACRONIX,        # Super Mario Advance 4: Super Mario Bros. 3 (Flash 1M)
    "A3A": SaveTypeID.EEPROM_64K,               # Super Mario Advance 2: Super Mario World (EEPROM 64K)
    "A2Y": SaveTypeID.EEPROM_64K,               # Yoshi's Island: Super Mario Advance 3 (EEPROM 64K)
    "AMA": SaveTypeID.EEPROM_4K,                # Super Mario Advance (EEPROM 4K)
    "A88": SaveTypeID.EEPROM_64K,               # Mario & Luigi: Superstar Saga (EEPROM 64K)
    "AZL": SaveTypeID.EEPROM_64K,               # Zelda: A Link to the Past & Four Swords (EEPROM 64K)
    "BZM": SaveTypeID.SRAM_256K,                # Zelda: The Minish Cap (SRAM 32K)
    "AMT": SaveTypeID.SRAM_256K,                # Metroid Fusion (early SRAM / later Flash)
    "BMX": SaveTypeID.SRAM_256K,                # Metroid: Zero Mission (SRAM 32K)
    "AGS": SaveTypeID.FLASH_512K_ATMEL,         # Golden Sun (Flash 512K)
    "AGF": SaveTypeID.FLASH_512K_ATMEL,         # Golden Sun: The Lost Age (Flash 512K)
    "AMK": SaveTypeID.FLASH_512K_ATMEL,         # Mario Kart: Super Circuit (Flash 512K)
    "AFX": SaveTypeID.FLASH_512K_ATMEL,         # Final Fantasy Tactics Advance (Flash 512K)
    "AE7": SaveTypeID.SRAM_256K,                # Fire Emblem (SRAM 32K)
    "BE8": SaveTypeID.SRAM_256K,                # Fire Emblem: The Sacred Stones (SRAM 32K)
    "AF6": SaveTypeID.SRAM_256K,                # Fire Emblem: The Binding Blade (SRAM 32K)
    "A3U": SaveTypeID.FLASH_512K_ATMEL,         # Mother 3 (Flash 512K)
}

REGION_MAP: Dict[str, str] = {
    "J": "Japan",
    "E": "USA",
    "P": "Europe",
    "D": "Germany",
    "F": "France",
    "I": "Italy",
    "S": "Spain",
    "U": "Australia",
    "K": "Korea",
}


@dataclasses.dataclass
class GBARomInfo:
    """Parsed metadata and hardware characteristics of a GBA ROM."""

    file_path: str
    raw_size: int
    padded_size: int
    crc32: int
    md5: str
    sha1: str
    internal_title: str
    game_code: str
    maker_code: str
    version: int
    header_checksum: int
    computed_checksum: int
    header_valid: bool
    logo_valid: bool
    sdk_save_strings: List[str]
    has_rtc: bool
    save_type_id: int
    save_type_name: str
    save_data_size: int
    title_id: int
    region: str
    system: str = "gba"
    system_name: str = "Game Boy Advance"
    lookup_crc32: int = 0
    product_prefix: str = "CTR-P"
    core_version: int = 0x00000202

    @property
    def crc32_hex(self) -> str:
        return f"{self.crc32:08X}"

    @property
    def lookup_crc32_hex(self) -> str:
        return f"{(self.lookup_crc32 or self.crc32):08X}"

    @property
    def title_id_hex(self) -> str:
        return f"{self.title_id:016X}"

    @property
    def product_code(self) -> str:
        serial = "".join(c for c in self.game_code[:4].upper() if c.isalnum()) or "AGBA"
        return f"{self.product_prefix}-{serial}"


def next_power_of_two_rom_size(size: int) -> int:
    """
    Calculate the padded GBA ROM size required by AGB_FIRM.
    Minimum is 1 MB (0x100000), maximum is 32 MB (0x2000000), always a power of 2.
    """
    target = 1024 * 1024  # 1 MB minimum
    while target < size and target < MAX_GBA_ROM_SIZE:
        target <<= 1
    return target


def pad_gba_rom(rom_bytes: bytes, pad_byte: int = 0xFF) -> bytes:
    """
    Pad a raw GBA ROM to the next power-of-two size (1MB..32MB) with 0xFF.
    """
    target_size = next_power_of_two_rom_size(len(rom_bytes))
    if len(rom_bytes) == target_size:
        return rom_bytes
    if len(rom_bytes) > target_size:
        return rom_bytes[:target_size]
    pad_len = target_size - len(rom_bytes)
    return rom_bytes + bytes([pad_byte & 0xFF]) * pad_len


def compute_header_checksum(rom_bytes: bytes) -> int:
    """
    Compute the GBA header complement check at 0xBD over bytes 0xA0..0xBC.
    """
    if len(rom_bytes) < 0xBE:
        return 0
    total = sum(rom_bytes[0xA0:0xBD])
    return (-(total + 0x19)) & 0xFF


def detect_save_type(
    rom_bytes: bytes,
    game_code: str,
    padded_size: int,
    override_save_type: Optional[str] = None,
    override_rtc: Optional[bool] = None,
) -> Tuple[int, List[str], bool]:
    """
    Detect the AGB_FIRM Save Type ID (0x00..0x0F), found SDK save strings, and RTC flag.
    """
    found_Raw = SAVE_STRING_REGEX.findall(rom_bytes)
    sdk_strings = sorted({s.decode("ascii", errors="ignore") for s in found_Raw})
    prefix3 = game_code[:3].upper()

    has_rtc = bool(RTC_STRING_REGEX.search(rom_bytes)) or (prefix3 in KNOWN_RTC_PREFIXES)
    if override_rtc is not None:
        has_rtc = override_rtc

    is_256m = padded_size >= (32 * 1024 * 1024)

    # 1. Explicit user CLI override
    if override_save_type and override_save_type.lower() != "auto":
        key = override_save_type.lower().strip()
        if key in ("sram", "sram256", "fram"):
            return SaveTypeID.SRAM_256K, sdk_strings, has_rtc
        if key in ("flash", "flash512", "flash64k"):
            return (SaveTypeID.FLASH_512K_PANASONIC_RTC if has_rtc else SaveTypeID.FLASH_512K_PANASONIC), sdk_strings, has_rtc
        if key in ("flash1m", "flash128k"):
            return (SaveTypeID.FLASH_1M_MACRONIX_RTC if has_rtc else SaveTypeID.FLASH_1M_MACRONIX), sdk_strings, has_rtc
        if key in ("eeprom4k", "eeprom512"):
            return (SaveTypeID.EEPROM_4K_256M if is_256m else SaveTypeID.EEPROM_4K), sdk_strings, has_rtc
        if key in ("eeprom", "eeprom64k", "eeprom8k"):
            return (SaveTypeID.EEPROM_64K_256M if is_256m else SaveTypeID.EEPROM_64K), sdk_strings, has_rtc
        if key in ("none", "nosave"):
            return SaveTypeID.NONE, sdk_strings, False
        if key.startswith("0x") or key.isdigit():
            val = int(key, 0) & 0x0F
            return val, sdk_strings, has_rtc

    # 2. Scan SDK strings in the ROM first (handles revisions like Metroid Fusion SRAM vs Flash)
    joined = " ".join(sdk_strings)
    if "FLASH1M_" in joined:
        save_id = SaveTypeID.FLASH_1M_MACRONIX_RTC if has_rtc else SaveTypeID.FLASH_1M_MACRONIX
        return save_id, sdk_strings, has_rtc

    if "FLASH512_" in joined or "FLASH_" in joined:
        save_id = SaveTypeID.FLASH_512K_PANASONIC_RTC if has_rtc else SaveTypeID.FLASH_512K_PANASONIC
        return save_id, sdk_strings, has_rtc

    if "SRAM_" in joined:
        return SaveTypeID.SRAM_256K, sdk_strings, has_rtc

    if "EEPROM_" in joined:
        # Classic NES Series (game_code starts with 'F') or known 4K EEPROM games
        if game_code.upper().startswith("F") or prefix3 in EEPROM_4K_PREFIXES:
            save_id = SaveTypeID.EEPROM_4K_256M if is_256m else SaveTypeID.EEPROM_4K
        else:
            save_id = SaveTypeID.EEPROM_64K_256M if is_256m else SaveTypeID.EEPROM_64K
        return save_id, sdk_strings, has_rtc

    # 3. Fallback to explicit game code table if SDK string was stripped/modified
    if prefix3 in EXPLICIT_SAVE_OVERRIDES:
        save_id = EXPLICIT_SAVE_OVERRIDES[prefix3]
        if save_id == SaveTypeID.EEPROM_4K and is_256m:
            save_id = SaveTypeID.EEPROM_4K_256M
        elif save_id == SaveTypeID.EEPROM_64K and is_256m:
            save_id = SaveTypeID.EEPROM_64K_256M
        return save_id, sdk_strings, has_rtc

    # 4. Default to SRAM 256K if unknown homebrew, or NONE if no indicators
    return SaveTypeID.SRAM_256K, sdk_strings, has_rtc


def save_data_size_for_type(save_type_id: int) -> int:
    """
    Return the SaveDataSize field for ExHeader (0x1C0) and TMD (0x19A):
      `max(0x30000, 2 * (0x200 + save_bytes))`
    - 0x00030000 (192 KB) for all <= 64 KB saves (EEPROM, SRAM, Flash 512K, None)
    - 0x00040400 (263,168 B) for 128 KB saves (Flash 1M)
    """
    if save_type_id in (
        SaveTypeID.FLASH_1M_MACRONIX_RTC,
        SaveTypeID.FLASH_1M_MACRONIX,
        SaveTypeID.FLASH_1M_SANYO_RTC,
        SaveTypeID.FLASH_1M_SANYO,
    ):
        return 0x00040400  # 2 * (0x200 + 0x20000)
    return 0x00030000


def generate_title_id(game_code: str, crc32: int, custom_unique_id: Optional[int] = None) -> int:
    """
    Generate a deterministic 3DS Application TitleID (0x000400000XXXXX00) in the safe
    GBA Virtual Console / Homebrew range (UniqueID 0x04000..0x0F7FF).
    """
    if custom_unique_id is not None:
        uid = custom_unique_id & 0xFFFFF
        return 0x0004000000000000 | (uid << 8)

    # Combine 4-char game_code and CRC32 for a collision-free, deterministic 16-bit ID
    seed = f"{game_code.upper()}:{crc32:08X}".encode("ascii")
    digest = hashlib.sha256(seed).digest()
    raw_16 = struct.unpack("<H", digest[:2])[0]
    # Place inside 0x0004000008XXXX00 (UniqueID 0x80000..0x8FFFF, well clear of retail 3DS cart/eShop titles)
    uid = 0x80000 | raw_16
    return 0x0004000000000000 | (uid << 8)


def apply_ips_patch(rom_bytes: bytes, ips_bytes: bytes) -> bytes:
    """
    Apply a standard IPS patch (PATCH ... EOF) to a GBA ROM buffer.
    """
    if not ips_bytes.startswith(b"PATCH"):
        raise ValueError("Invalid IPS patch: missing 'PATCH' magic header")

    out = bytearray(rom_bytes)
    pos = 5
    n = len(ips_bytes)

    while pos < n:
        if ips_bytes[pos : pos + 3] == b"EOF":
            break
        if pos + 5 > n:
            raise ValueError("Truncated IPS patch record")
        offset = (ips_bytes[pos] << 16) | (ips_bytes[pos + 1] << 8) | ips_bytes[pos + 2]
        size = (ips_bytes[pos + 3] << 8) | ips_bytes[pos + 4]
        pos += 5

        if size == 0:
            # RLE record
            if pos + 3 > n:
                raise ValueError("Truncated IPS RLE record")
            rle_size = (ips_bytes[pos] << 8) | ips_bytes[pos + 1]
            rle_byte = ips_bytes[pos + 2]
            pos += 3
            if offset + rle_size > len(out):
                out.extend(b"\xFF" * (offset + rle_size - len(out)))
            out[offset : offset + rle_size] = bytes([rle_byte]) * rle_size
        else:
            if pos + size > n:
                raise ValueError("Truncated IPS data payload")
            if offset + size > len(out):
                out.extend(b"\xFF" * (offset + size - len(out)))
            out[offset : offset + size] = ips_bytes[pos : pos + size]
            pos += size

    return bytes(out)


def parse_gba_rom(
    file_path: str,
    rom_bytes: Optional[bytes] = None,
    override_save_type: Optional[str] = None,
    override_rtc: Optional[bool] = None,
    custom_unique_id: Optional[int] = None,
) -> GBARomInfo:
    """
    Parse a GBA ROM file or byte buffer and extract header, checksums, and AGB_FIRM parameters.
    """
    if rom_bytes is None:
        with open(file_path, "rb") as f:
            rom_bytes = f.read()

    raw_size = len(rom_bytes)
    if raw_size < MIN_GBA_ROM_SIZE:
        raise ValueError(f"File is too small to be a valid GBA ROM ({raw_size} bytes)")
    if raw_size > MAX_GBA_ROM_SIZE:
        raise ValueError(
            f"GBA ROM size ({raw_size} bytes) exceeds 32 MB AGB_FIRM hardware limit"
        )

    padded_size = next_power_of_two_rom_size(raw_size)
    crc32 = zlib.crc32(rom_bytes) & 0xFFFFFFFF
    md5 = hashlib.md5(rom_bytes).hexdigest().upper()
    sha1 = hashlib.sha1(rom_bytes).hexdigest().upper()

    raw_title = rom_bytes[0xA0:0xAC]
    internal_title = raw_title.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()

    raw_code = rom_bytes[0xAC:0xB0]
    game_code = "".join(
        chr(b) if 32 <= b <= 126 else "" for b in raw_code
    ).strip() or "AGBA"

    raw_maker = rom_bytes[0xB0:0xB2]
    maker_code = "".join(
        chr(b) if 32 <= b <= 126 else "" for b in raw_maker
    ).strip() or "01"

    version = rom_bytes[0xBC]
    header_checksum = rom_bytes[0xBD]
    computed_checksum = compute_header_checksum(rom_bytes)
    header_valid = (header_checksum == computed_checksum) and (rom_bytes[0xB2] == 0x96)
    logo_valid = rom_bytes[0x04:0x14] == NINTENDO_LOGO_PREFIX

    save_type_id, sdk_strings, has_rtc = detect_save_type(
        rom_bytes,
        game_code=game_code,
        padded_size=padded_size,
        override_save_type=override_save_type,
        override_rtc=override_rtc,
    )
    save_type_name = SAVE_TYPE_NAMES.get(save_type_id, f"Unknown ({save_type_id:#x})")
    save_data_size = save_data_size_for_type(save_type_id)
    title_id = generate_title_id(game_code, crc32, custom_unique_id=custom_unique_id)

    region_char = game_code[3:4].upper() if len(game_code) >= 4 else "E"
    region = REGION_MAP.get(region_char, "World")

    return GBARomInfo(
        file_path=file_path,
        raw_size=raw_size,
        padded_size=padded_size,
        crc32=crc32,
        md5=md5,
        sha1=sha1,
        internal_title=internal_title,
        game_code=game_code,
        maker_code=maker_code,
        version=version,
        header_checksum=header_checksum,
        computed_checksum=computed_checksum,
        header_valid=header_valid,
        logo_valid=logo_valid,
        sdk_save_strings=sdk_strings,
        has_rtc=has_rtc,
        save_type_id=save_type_id,
        save_type_name=save_type_name,
        save_data_size=save_data_size,
        title_id=title_id,
        region=region,
    )
