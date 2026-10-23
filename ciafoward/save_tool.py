"""
GBA Virtual Console (AGB_FIRM) Save File (.sav / .srm) Converter, Validator & Inspector.
Handles:
  - Converting RetroArch (.srm), mGBA (.sav), and VBA (.sav) saves to exact 3DS AGB_FIRM hardware size
  - Stripping trailing RTC timestamps or RetroArch 128KB SRAM/Flash over-padding
  - Swapping 8-byte (64-bit) EEPROM endianness blocks between VBA/emulators and 3DS AGB_FIRM
  - Computing the exact 3DS SD card Title save path (`title/00040000/<tid_low>/data/00000001.sav`)
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Dict, Optional, Tuple

from .cia import verify_and_inspect_cia
from .gba import SAVE_TYPE_NAMES, SaveTypeID, parse_gba_rom

# Exact raw hardware save size (in bytes) expected by GodMode9 "Inject GBA VC save" / AGB_FIRM
HARDWARE_SAVE_BYTES: Dict[int, int] = {
    SaveTypeID.EEPROM_4K: 512,               # 0x200 (4 kbit)
    SaveTypeID.EEPROM_4K_256M: 512,          # 0x200 (4 kbit)
    SaveTypeID.EEPROM_64K: 8 * 1024,         # 0x2000 (64 kbit)
    SaveTypeID.EEPROM_64K_256M: 8 * 1024,    # 0x2000 (64 kbit)
    SaveTypeID.FLASH_512K_ATMEL: 64 * 1024,  # 0x10000 (512 kbit)
    SaveTypeID.FLASH_512K_MACRONIX: 64 * 1024,
    SaveTypeID.FLASH_512K_ATMEL_RTC: 64 * 1024,
    SaveTypeID.FLASH_512K_MACRONIX_RTC: 64 * 1024,
    SaveTypeID.FLASH_1M_MACRONIX: 128 * 1024,  # 0x20000 (1 Mbit)
    SaveTypeID.FLASH_1M_SANYO: 128 * 1024,
    SaveTypeID.FLASH_1M_MACRONIX_RTC: 128 * 1024,
    SaveTypeID.FLASH_1M_SANYO_RTC: 128 * 1024,
    SaveTypeID.SRAM_256K_RTC: 32 * 1024,     # 0x8000 (256 kbit)
    SaveTypeID.SRAM_256K_ALT: 32 * 1024,
    SaveTypeID.SRAM_256K: 32 * 1024,
    SaveTypeID.NONE: 0,
}


@dataclasses.dataclass
class SaveConversionResult:
    """Result of preparing a GBA save file for 3DS AGB_FIRM injection."""

    input_save_path: str
    output_save_path: str
    input_size: int
    output_size: int
    save_type_id: int
    save_type_name: str
    is_eeprom: bool
    eeprom_swapped: bool
    rtc_footer_stripped: bool
    overpadding_stripped: bool
    title_id_hex: str
    title_id_high: str
    title_id_low: str
    sd_save_rel_path: str
    game_code: str


def swap_eeprom_8byte_blocks(data: bytes) -> bytes:
    """
    Reverse every 8-byte (64-bit) chunk in an EEPROM save buffer.
    Used when converting between VisualBoyAdvance / older emulators and 3DS AGB_FIRM.
    """
    if len(data) % 8 != 0:
        raise ValueError(f"EEPROM save length ({len(data)}) must be a multiple of 8 bytes")
    out = bytearray(len(data))
    for i in range(0, len(data), 8):
        out[i : i + 8] = data[i : i + 8][::-1]
    return bytes(out)


def resolve_target_save_spec(target_path: str) -> Tuple[int, str, str]:
    """
    Given a `.gba` or `.cia` file path, return (save_type_id, title_id_hex, game_code).
    """
    p = Path(target_path).expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(f"Target ROM or CIA file not found: {target_path}")

    if p.suffix.lower() == ".cia":
        info = verify_and_inspect_cia(str(p))
        caa = info.get("caa_footer", {})
        save_type_id = caa.get("save_type_id", SaveTypeID.SRAM_256K)
        title_id_hex = info["title_id"]
        product_code = info.get("product_code", "CTR-P-AGBA")
        game_code = product_code.split("-")[-1] if "-" in product_code else "AGBA"
        return save_type_id, title_id_hex, game_code

    rom_info = parse_gba_rom(str(p))
    return rom_info.save_type_id, rom_info.title_id_hex, rom_info.game_code


def convert_save_for_3ds(
    target_rom_or_cia: str,
    input_save_path: str,
    output_save_path: Optional[str] = None,
    swap_eeprom: bool = False,
    override_save_type_id: Optional[int] = None,
) -> SaveConversionResult:
    """
    Normalize a `.sav` or `.srm` file from an emulator (mGBA, RetroArch, VBA, etc.)
    into a clean 3DS AGB_FIRM raw save file ready for GodMode9 injection.
    """
    save_type_id, title_id_hex, game_code = resolve_target_save_spec(target_rom_or_cia)
    if override_save_type_id is not None:
        save_type_id = override_save_type_id

    expected_size = HARDWARE_SAVE_BYTES.get(save_type_id, 64 * 1024)
    if expected_size == 0:
        raise ValueError("This game is configured with SaveType=NONE (no save hardware).")

    in_p = Path(input_save_path).expanduser().resolve()
    if not in_p.is_file():
        raise FileNotFoundError(f"Input save file not found: {input_save_path}")

    raw = in_p.read_bytes()
    input_size = len(raw)
    rtc_stripped = False
    overpadding_stripped = False

    # 1. Strip 16-byte or 48-byte mGBA/VBA-M RTC trailer if present
    if input_size in (expected_size + 16, expected_size + 48):
        raw = raw[:expected_size]
        rtc_stripped = True
    # 2. Strip RetroArch libretro (.srm) over-padding (e.g. 128KB or 136KB container for 64KB/32KB/8KB/512B save)
    elif input_size > expected_size:
        raw = raw[:expected_size]
        overpadding_stripped = True
    # 3. Pad with 0xFF (unprogrammed Flash/EEPROM state) if smaller than expected hardware size
    elif input_size < expected_size:
        raw = raw + b"\xFF" * (expected_size - input_size)

    is_eeprom = save_type_id in (
        SaveTypeID.EEPROM_4K,
        SaveTypeID.EEPROM_4K_256M,
        SaveTypeID.EEPROM_64K,
        SaveTypeID.EEPROM_64K_256M,
    )
    eeprom_swapped = False
    if is_eeprom and swap_eeprom:
        raw = swap_eeprom_8byte_blocks(raw)
        eeprom_swapped = True

    if output_save_path:
        out_p = Path(output_save_path).expanduser().resolve()
    else:
        out_p = in_p.with_name(f"{in_p.stem}_3ds_{game_code}.sav")

    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_bytes(raw)

    tid_high = title_id_hex[:8].lower()
    tid_low = title_id_hex[8:].lower()
    sd_save_rel_path = f"Nintendo 3DS/<ID0>/<ID1>/title/{tid_high}/{tid_low}/data/00000001.sav"

    return SaveConversionResult(
        input_save_path=str(in_p),
        output_save_path=str(out_p),
        input_size=input_size,
        output_size=len(raw),
        save_type_id=save_type_id,
        save_type_name=SAVE_TYPE_NAMES.get(save_type_id, f"0x{save_type_id:02X}"),
        is_eeprom=is_eeprom,
        eeprom_swapped=eeprom_swapped,
        rtc_footer_stripped=rtc_stripped,
        overpadding_stripped=overpadding_stripped,
        title_id_hex=title_id_hex,
        title_id_high=tid_high,
        title_id_low=tid_low,
        sd_save_rel_path=sd_save_rel_path,
        game_code=game_code,
    )
