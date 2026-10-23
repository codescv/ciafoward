"""
3DS GBA Virtual Console (AGB_FIRM) CIA Packager & Verifier.
Implements:
  - Authentic 0x360-byte (864-byte) AGB_FIRM `.CAA` Footer (0x324 Config Block + 256-entry RGB LUT + Descriptors + .CAA Header)
  - ExeFS Builder & Verifier (.code + banner + icon + logo with reverse-indexed SHA-256 table)
  - NCCH Builder & Verifier (NoCrypto, AGB_FIRM ExHeader with signed AccessDescriptor, 0x1000-aligned RomFS)
  - CIA Archive Builder & Verifier (Header + Retail CertChain + Signed Ticket + Signed TMD + NCCH + Meta)
"""

from __future__ import annotations

import dataclasses
import hashlib
import struct
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .banner import (
    build_cbmd_banner,
    build_smdh,
    decompress_lz11,
    parse_smdh,
)
from .consoles import (
    build_snes_vc_data_bin,
    convert_ines_to_tnes,
    parse_gb_rom,
    parse_md_rom,
    parse_nes_rom,
    parse_snes_rom,
)
from .gba import SAVE_TYPE_NAMES, GBARomInfo, SaveTypeID, parse_gba_rom
from .graphics import (
    RGBAImage,
    compose_vc_banner_256x128,
    compose_vc_icons,
    generate_stylized_cover,
    load_image_file,
)
from .metadata import GameMetadata
from .romfs import build_romfs, extract_romfs, verify_romfs_ivfc
from .templates import (
    build_console_exheader,
    build_gbavc_exheader,
    get_banner_cgfx_header,
    get_cert_chain,
    get_config_block_template,
    get_console_template,
    get_logo_bytes,
    get_ncch_header_template,
    get_romfs_bytes,
    get_templates_dir,
    get_ticket_template,
    get_tmd_template,
)

MEDIA_UNIT = 0x200  # 512 bytes
CIA_ALIGN = 0x40    # 64 bytes
RETAIL_CERT_CHAIN_MD5 = "03de8229c72a836680d71a31436fdfb0"


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def pad_bytes(data: bytes, alignment: int, pad_byte: bytes = b"\x00") -> bytes:
    rem = len(data) % alignment
    if rem == 0:
        return data
    return data + pad_byte * (alignment - rem)


def build_caa_footer(
    rom_size: int,
    save_type: int,
    ghosting: int = 0x00,
    dark_filter: bool = False,
    rom_offset: int = 0,
) -> bytes:
    """
    Build the authentic 0x360-byte (864-byte) AGB_FIRM `.CAA` footer placed immediately
    after the 16-byte-aligned GBA ROM inside `ExeFS:/.code`:
      - `0x000..0x324` (804 B): AGB Config Block:
          * `0x000..0x010`: `<IIIHH` `(0, rom_size, save_type, 0xFFFF, 0)`
          * `0x010..0x020`: `<4I` Save chip timing parameters
          * `0x020..0x024`: `<I` LCD ghosting (`0xFF` = sharp/none, `0x01..0xFE` = ghosting intensity)
          * `0x024..0x324`: 256 * 3 = 768-byte RGB `videoLUT`
      - `0x324..0x330` (12 B) : Zero padding
      - `0x330..0x340` (16 B) : Section 0 descriptor `<4I` `(0, rom_offset, rom_size, 0)`
      - `0x340..0x350` (16 B) : Section 1 descriptor `<4I` `(1, rom_offset + rom_size, 0x324, 0)`
      - `0x350..0x360` (16 B) : `.CAA` header `<4sIII` `(b".CAA", 1, rom_offset + rom_size + 0x330, 2 << 4)`
    """
    footer = bytearray(0x360)
    cfg_tpl = get_config_block_template()
    footer[0x000:0x324] = cfg_tpl[:0x324]

    struct.pack_into("<IIIHH", footer, 0x000, 0, rom_size, save_type & 0x0F, 0xFFFF, 0)

    # Save chip timing parameters (64kbit EEPROM / 1Mbit Flash vs 4kbit EEPROM / 512kbit Flash / SRAM)
    if (save_type & 0x0F) in (
        SaveTypeID.EEPROM_64K,
        SaveTypeID.EEPROM_64K_256M,
        SaveTypeID.FLASH_1M_MACRONIX_RTC,
        SaveTypeID.FLASH_1M_MACRONIX,
        SaveTypeID.FLASH_1M_SANYO_RTC,
        SaveTypeID.FLASH_1M_SANYO,
    ):
        struct.pack_into("<4I", footer, 0x010, 0x17D43E, 0x26206, 0x86, 0x2DD13)
    else:
        struct.pack_into("<4I", footer, 0x010, 0x27C886, 0x8CE35, 0x184, 0x31170)

    # In AGB_FIRM: 0xFF (255) = no ghosting (sharp LCD), 0x80 (128) = Ambassador ghosting
    agb_ghost = 0xFF if ghosting <= 0 else max(1, min(0xFF, ghosting))
    struct.pack_into("<I", footer, 0x020, agb_ghost)

    # 256-entry RGB videoLUT at 0x024..0x324 (768 bytes)
    if not dark_filter:
        for i in range(256):
            off = 0x024 + i * 3
            footer[off] = i
            footer[off + 1] = i
            footer[off + 2] = i

    cfg_offset = rom_offset + rom_size
    table_offset = cfg_offset + 0x330
    struct.pack_into("<4I", footer, 0x330, 0, rom_offset, rom_size, 0)
    struct.pack_into("<4I", footer, 0x340, 1, cfg_offset, 0x324, 0)
    struct.pack_into("<4sIII", footer, 0x350, b".CAA", 1, table_offset, 2 << 4)
    return bytes(footer)


def build_gbavc_code_section(
    rom_bytes: bytes,
    save_type_id: int,
    ghosting: int = 0x00,
    dark_filter: bool = False,
) -> Tuple[bytes, int, int]:
    """
    Assemble `ExeFS:/.code` = `[16-byte Padded GBA ROM at offset 0] + [0x360-byte .CAA Footer]`.
    Note: AGB_FIRM requires the GBA ROM section to begin at offset 0 of `.code`.
    Returns `(code_bytes, rom_offset, padded_rom_size)`.
    """
    padded_rom = pad_bytes(rom_bytes, 0x10, b"\xFF")
    rom_offset = 0
    padded_size = len(padded_rom)
    footer = build_caa_footer(
        rom_size=padded_size,
        save_type=save_type_id,
        ghosting=ghosting,
        dark_filter=dark_filter,
        rom_offset=rom_offset,
    )
    return padded_rom + footer, rom_offset, padded_size


def build_exefs(files: List[Tuple[str, bytes]]) -> bytes:
    """
    Build a 3DS ExeFS image from a list of `(filename, raw_bytes)` pairs.
    File hashes are stored in reverse order at `0x200 - (idx + 1) * 0x20`.
    """
    if len(files) > 10:
        raise ValueError("ExeFS supports at most 10 files")

    header = bytearray(0x200)
    body = bytearray()
    current_offset = 0

    for idx, (name, content) in enumerate(files):
        name_bytes = name.encode("ascii")[:8].ljust(8, b"\x00")
        file_size = len(content)

        entry_offset = idx * 0x10
        header[entry_offset : entry_offset + 8] = name_bytes
        struct.pack_into("<II", header, entry_offset + 8, current_offset, file_size)

        sha_offset = 0x200 - (idx + 1) * 0x20
        header[sha_offset : sha_offset + 0x20] = hashlib.sha256(content).digest()

        padded_content = pad_bytes(content, MEDIA_UNIT, b"\x00")
        body.extend(padded_content)
        current_offset += len(padded_content)

    return bytes(header + body)


def build_ncch(
    title_id: int,
    game_code: str,
    exheader_bytes: bytes,
    exefs_bytes: bytes,
    romfs_bytes: Optional[bytes] = None,
    maker_code: str = "01",
    product_prefix: str = "CTR-P",
) -> bytes:
    """
    Build an unencrypted (`NoCrypto`) 3DS NCCH (`.cxi` / Content 0) container,
    including the signed NCCH header template, signed ExHeader, ExeFS, and 0x1000-aligned RomFS.
    """
    if len(exheader_bytes) != 0x800:
        raise ValueError(f"ExHeader must be 0x800 bytes, got {len(exheader_bytes):#x}")
    if len(exefs_bytes) % MEDIA_UNIT != 0:
        exefs_bytes = pad_bytes(exefs_bytes, MEDIA_UNIT)
    if romfs_bytes is None:
        romfs_bytes = get_romfs_bytes()
    if len(romfs_bytes) % MEDIA_UNIT != 0:
        romfs_bytes = pad_bytes(romfs_bytes, MEDIA_UNIT)

    ncch_header_size = 0x200
    exheader_total_size = 0x800
    logo_region_bytes = pad_bytes(get_logo_bytes(), MEDIA_UNIT)  # 0x2000 bytes (16 MU)
    plain_strings = (
        b"[SDK+NINTENDO:CTR_SDK-11_4_0_200_none]\x00"
        b"[SDK+NINTENDO:Firmware-02_50_8]\x00"
        b"[SDK+NINTENDO:ExtraPad]\x00"
        b"[SDK+NINTENDO:NW4C_3_7_4_fnt1]\x00"
        b"[SDK+NINTENDO:NW4C_3_7_4_lyt1]\x00"
        b"[SDK+NINTENDO:NW4C_3_7_4_snd]\x00"
        b"[SDK+NINTENDO:Ir]\x00"
    )
    plain_region_bytes = plain_strings.ljust(MEDIA_UNIT, b"\x00")[:MEDIA_UNIT]  # 0x200 bytes (1 MU)

    logo_offset_bytes = ncch_header_size + exheader_total_size  # 0xA00 (MU 5)
    plain_offset_bytes = logo_offset_bytes + len(logo_region_bytes)  # 0x2A00 (MU 21)
    exefs_offset_bytes = plain_offset_bytes + len(plain_region_bytes)  # 0x2C00 (MU 22)
    after_exefs = exefs_offset_bytes + len(exefs_bytes)
    romfs_offset_bytes = align_up(after_exefs, 0x1000)
    exefs_to_romfs_pad = b"\x00" * (romfs_offset_bytes - after_exefs)
    total_ncch_bytes = romfs_offset_bytes + len(romfs_bytes)

    hdr = bytearray(get_ncch_header_template())
    hdr[0x100:0x104] = b"NCCH"
    struct.pack_into("<I", hdr, 0x104, total_ncch_bytes // MEDIA_UNIT)
    struct.pack_into("<Q", hdr, 0x108, title_id)
    hdr[0x110:0x112] = maker_code.encode("ascii")[:2].ljust(2, b"0")
    struct.pack_into("<H", hdr, 0x112, 0x0002)
    struct.pack_into("<I", hdr, 0x114, 0x00000000)
    struct.pack_into("<Q", hdr, 0x118, title_id)

    # Logo Region SHA-256 (0x130..0x150) — verified by Process9 FS when logoSize > 0
    hdr[0x130:0x150] = hashlib.sha256(logo_region_bytes).digest()

    clean_serial = "".join(c for c in game_code[:4].upper() if c.isalnum()) or "AGBA"
    product_code = f"{product_prefix}-{clean_serial}".encode("ascii")[:16].ljust(16, b"\x00")
    hdr[0x150:0x160] = product_code

    # SHA-256 of first 0x400 bytes of ExHeader (SCI + ACI)
    hdr[0x160:0x180] = hashlib.sha256(exheader_bytes[:0x400]).digest()
    struct.pack_into("<I", hdr, 0x180, 0x00000400)
    struct.pack_into("<I", hdr, 0x184, 0x00000000)

    # NCCH Flags (0x188..0x18F)
    hdr[0x188 + 3] = 0x00  # Crypto method
    hdr[0x188 + 4] = 0x01  # Content Platform: 1 = CTR
    hdr[0x188 + 5] = 0x03  # Content Type: Data (0x1) | Executable (0x2) = 0x03
    hdr[0x188 + 6] = 0x00  # Media Unit Size: 0 => 0x200 bytes
    hdr[0x188 + 7] = 0x04  # BitFlags: 0x04 = NoCrypto

    # PlainRegion (SDK/ExtraPad/Ir declarations) & LogoRegion
    struct.pack_into("<II", hdr, 0x190, plain_offset_bytes // MEDIA_UNIT, len(plain_region_bytes) // MEDIA_UNIT)
    struct.pack_into("<II", hdr, 0x198, logo_offset_bytes // MEDIA_UNIT, len(logo_region_bytes) // MEDIA_UNIT)

    # ExeFS Offset, Size, Hash Region Size (in media units)
    struct.pack_into("<I", hdr, 0x1A0, exefs_offset_bytes // MEDIA_UNIT)
    struct.pack_into("<I", hdr, 0x1A4, len(exefs_bytes) // MEDIA_UNIT)
    struct.pack_into("<I", hdr, 0x1A8, 0x00000001)
    struct.pack_into("<I", hdr, 0x1AC, 0x00000000)

    # RomFS Offset, Size, Hash Region Size (in media units)
    struct.pack_into("<I", hdr, 0x1B0, romfs_offset_bytes // MEDIA_UNIT)
    struct.pack_into("<I", hdr, 0x1B4, len(romfs_bytes) // MEDIA_UNIT)
    struct.pack_into("<I", hdr, 0x1B8, 0x00000001)
    struct.pack_into("<I", hdr, 0x1BC, 0x00000000)

    # ExeFS Superblock SHA-256 (first 0x200 bytes of ExeFS)
    hdr[0x1C0:0x1E0] = hashlib.sha256(exefs_bytes[:MEDIA_UNIT]).digest()
    # RomFS Superblock SHA-256 (first 0x200 bytes of RomFS)
    hdr[0x1E0:0x200] = hashlib.sha256(romfs_bytes[:MEDIA_UNIT]).digest()

    return bytes(
        hdr
        + exheader_bytes
        + logo_region_bytes
        + plain_region_bytes
        + exefs_bytes
        + exefs_to_romfs_pad
        + romfs_bytes
    )


def build_cert_chain() -> bytes:
    """
    Return the authentic 0xA00-byte retail 3DS Certificate Chain
    (`CA00000003` + `XS0000000c` + `CP0000000b`, MD5 `03de8229c72a836680d71a31436fdfb0`).
    """
    return get_cert_chain()


def build_ticket(title_id: int, title_version: int = 0) -> bytes:
    """Build the 0x350-byte 3DS Ticket from the signed retail/VC template."""
    tik = bytearray(get_ticket_template())
    struct.pack_into(">Q", tik, 0x1DC, title_id)
    struct.pack_into(">H", tik, 0x1E6, title_version)
    return bytes(tik)


def build_tmd(
    title_id: int,
    ncch_bytes: bytes,
    save_data_size: int = 0x00030000,
    title_version: int = 0,
) -> bytes:
    """Build the 0xB34-byte 3DS Title Metadata (TMD) for 1 unencrypted NCCH content."""
    tmd = bytearray(get_tmd_template())
    struct.pack_into(">Q", tmd, 0x18C, title_id)
    # SaveDataSize at 0x19A in 3DS TMD is Little-Endian (<I)
    struct.pack_into("<I", tmd, 0x19A, save_data_size)
    struct.pack_into(">H", tmd, 0x1DC, title_version)
    struct.pack_into(">H", tmd, 0x1DE, 0x0001)

    # Content Chunk Record 0 (0xB04..0xB33, 0x30 bytes)
    struct.pack_into(">I", tmd, 0xB04, 0x00000000)
    struct.pack_into(">H", tmd, 0xB08, 0x0000)
    struct.pack_into(">H", tmd, 0xB0A, 0x0000)  # Unencrypted
    struct.pack_into(">Q", tmd, 0xB0C, len(ncch_bytes))
    tmd[0xB14:0xB34] = hashlib.sha256(ncch_bytes).digest()

    # Content Info Record 0 (0x204..0x227, 0x24 bytes)
    struct.pack_into(">H", tmd, 0x204, 0x0000)
    struct.pack_into(">H", tmd, 0x206, 0x0001)
    tmd[0x208:0x228] = hashlib.sha256(tmd[0xB04:0xB34]).digest()

    # Superhash of all 64 Content Info Records (0x204..0xB03) at 0x1E4..0x203
    tmd[0x1E4:0x204] = hashlib.sha256(tmd[0x204:0xB04]).digest()
    return bytes(tmd)


def build_cia_meta(exheader_bytes: bytes, smdh_bytes: bytes) -> bytes:
    """Build the 0x3AC0-byte CIA Meta section (used by FBI/installer for preview)."""
    meta = bytearray(0x3AC0)
    meta[0x000:0x180] = exheader_bytes[0x040:0x1C0]
    (core_ver,) = struct.unpack_from("<I", exheader_bytes, 0x208) if len(exheader_bytes) >= 0x20C else (0x00000202,)
    struct.pack_into("<I", meta, 0x180, core_ver)
    struct.pack_into("<I", meta, 0x300, core_ver)
    meta[0x400:0x3AC0] = smdh_bytes[:0x36C0]
    return bytes(meta)


def patch_md_code_for_2x_rgui(code_bytes: bytes) -> bytes:
    """
    Patch the PicoDrive 3DS RetroArch 1.3.2 `.code` binary so the bottom-screen RGUI menu:
      - Renders into a `200x120` framebuffer (instead of `320x240`) with `4px` margins and `9` items/page
      - Scales by an exact `2x` integer factor (`200x120 -> 400x240`) using `GPU_NEAREST` point sampling
        (instead of blurry `GPU_LINEAR` bilinear filtering)
      - Uses a clean solid dark slate background (`0x222E`) instead of a noisy 2x2 dithered checkerboard
      - Defaults to `"rgui"` even if `retroarch.cfg` is missing
    """
    buf = bytearray(code_bytes)
    patches = (
        (0x02EBC0, 0xE3A02D05, 0xE3A020C8),  # rgui_init: fb_width = 200
        (0x02EBC4, 0xE3A07D0A, 0xE3A07F64),  # rgui_init: fb_pitch = 400
        (0x02EBD0, 0xE3A0E0F0, 0xE3A0E078),  # rgui_init: fb_height = 120
        (0x02EC44, 0xE3A030F0, 0xE3A03000),  # rgui_init: rgui->last_height = 0
        (0x0C69E0, 0xE2033001, 0xE3A03000),  # rgui_render: solid dark background (no 2x2 checkerboard)
        (0x0C69F4, 0xE383300C, 0xE383300E),  # rgui_render: panel alpha = 0xE (0x222E)
        (0x0C6A88, 0xE1A03223, 0xE3A03004),  # rgui_render: term_start_x = 4
        (0x0C6A90, 0xE1A080A8, 0xE3A08010),  # rgui_render: term_start_y = 16
        (0x0C7000, 0xE1A03223, 0xE3A03004),  # rgui_render (fallback): term_start_x = 4
        (0x0C700C, 0xE1A080A8, 0xE3A08010),  # rgui_render (fallback): term_start_y = 16
        (0x0097C4, 0xE08220A1, 0xE3A02040),  # rgui_navigation: term_start_x << 4
        (0x0097C8, 0xE04330AE, 0xE2433010),  # rgui_navigation: height - term_start_y
        (0x020BDC, 0xE26B2E19, 0xE3A02000),  # ctr_set_texture_frame: x0 = 0
        (0x020BE4, 0xE1A020A2, 0xE3A02000),  # ctr_set_texture_frame: x0 = 0
        (0x020BE8, 0xE26830F0, 0xE3A03000),  # ctr_set_texture_frame: y0 = 0
        (0x020BEC, 0xE1A030A3, 0xE3A03000),  # ctr_set_texture_frame: y0 = 0
        (0x020BF0, 0xE082100B, 0xE1A0108B),  # ctr_set_texture_frame: x1 = width * 2 = 400
        (0x020BF8, 0xE0831008, 0xE1A01088),  # ctr_set_texture_frame: y1 = height * 2 = 240
        (0x051F14, 0xE3A03006, 0xE3A03000),  # ctr_frame: menu GPUREG_TEXUNIT0_PARAM = GPU_NEAREST
        (0x06BFBC, 0x00312814, 0x00346D2C),  # config_get_default_menu: "rgui"
        (0x0E5444, 0x00312814, 0x00346D2C),  # config_set_defaults: "rgui"
    )
    for offset, expected_orig, new_val in patches:
        if offset + 4 <= len(buf):
            (cur_val,) = struct.unpack_from("<I", buf, offset)
            if cur_val == expected_orig:
                struct.pack_into("<I", buf, offset, new_val)
    return bytes(buf)


def build_cia(
    title_id: int,
    ncch_bytes: bytes,
    exheader_bytes: bytes,
    smdh_bytes: bytes,
    save_data_size: int = 0x00030000,
) -> bytes:
    """Assemble the complete .cia archive with all sections aligned to 0x40 bytes."""
    cert_chain = build_cert_chain()
    ticket = build_ticket(title_id)
    tmd = build_tmd(title_id, ncch_bytes, save_data_size)
    meta = build_cia_meta(exheader_bytes, smdh_bytes)

    cia_hdr = bytearray(0x2020)
    struct.pack_into(
        "<IHHIIIIQ",
        cia_hdr,
        0x00,
        0x00002020,
        0x0000,
        0x0000,
        len(cert_chain),
        len(ticket),
        len(tmd),
        len(meta),
        len(ncch_bytes),
    )
    cia_hdr[0x0020] = 0x80  # Content 0 enabled

    parts = [
        pad_bytes(bytes(cia_hdr), CIA_ALIGN),
        pad_bytes(cert_chain, CIA_ALIGN),
        pad_bytes(ticket, CIA_ALIGN),
        pad_bytes(tmd, CIA_ALIGN),
        pad_bytes(ncch_bytes, CIA_ALIGN),
        pad_bytes(meta, CIA_ALIGN),
    ]
    return b"".join(parts)


@dataclasses.dataclass
class CIABuildResult:
    """Summary of a completed GBA -> CIA build."""

    output_cia_path: str
    cia_size: int
    cia_sha256: str
    rom_info: GBARomInfo
    metadata: GameMetadata
    rom_offset_in_code: int
    padded_rom_size: int
    ghosting: int
    dark_filter: bool
    banner_style: str
    code_stub_source: str
    cgfx_header_source: str
    preview_banner_png_path: Optional[str] = None
    preview_icon_png_path: Optional[str] = None


def package_gba_to_cia(
    rom_bytes: bytes,
    rom_info: GBARomInfo,
    metadata: GameMetadata,
    output_cia_path: str,
    ghosting: int = 0x00,
    dark_filter: bool = False,
    banner_style: str = "vc",
    custom_code_path: Optional[str] = None,
    download_templates: bool = True,
    save_preview_dir: Optional[str] = None,
) -> CIABuildResult:
    """
    Full pipeline:
      1. Compose 24x24 & 48x48 VC Icons and build 0x36C0 SMDH
      2. Compose VC Banner and build CBMD (3D GBA VC model or 2D 256x128 RGBA4 + GBA Chime BCWAV)
      3. Assemble ExeFS `.code` (16-byte Padded GBA ROM at offset 0 + 0x360 .CAA Footer)
      4. Build ExeFS (.code, banner, icon, logo), signed ExHeader, NCCH with RomFS, and final .cia
    """
    # 1. Build SMDH Icon
    icon_24, icon_48 = compose_vc_icons(metadata)
    smdh_bytes = build_smdh(
        short_title=metadata.short_title,
        long_title=metadata.long_title,
        publisher=metadata.publisher,
        icon_24x24=icon_24,
        icon_48x48=icon_48,
        system=rom_info.system,
    )

    # 2. Build CBMD Banner
    banner_img = compose_vc_banner_256x128(metadata, banner_style=banner_style)
    screen_img: Optional[RGBAImage] = None
    if metadata.title_screen_path and Path(metadata.title_screen_path).is_file():
        try:
            screen_img = load_image_file(metadata.title_screen_path)
        except Exception:
            screen_img = None
    if screen_img is None and metadata.boxart_path and Path(metadata.boxart_path).is_file():
        try:
            screen_img = load_image_file(metadata.boxart_path)
        except Exception:
            screen_img = None
    if screen_img is None:
        screen_img = generate_stylized_cover(metadata, 240, 160)

    subtitle = f"Released: {metadata.release_year}" if metadata.release_year else metadata.publisher
    cgfx_hdr, cgfx_src = get_banner_cgfx_header(download=download_templates)
    use_style = "3d" if banner_style in ("vc", "3d", "vc3d") else "2d"
    if use_style == "3d":
        cgfx_src = "bundled-3d-vc-cgfx"

    banner_bytes = build_cbmd_banner(
        banner_img=banner_img,
        cgfx_header=cgfx_hdr,
        banner_style=use_style,
        screen_img=screen_img,
        short_title=metadata.short_title,
        subtitle=subtitle,
        system=rom_info.system,
    )

    # Optional preview PNG export
    preview_banner_png: Optional[str] = None
    preview_icon_png: Optional[str] = None
    if save_preview_dir:
        from .graphics import encode_png_bytes

        pdir = Path(save_preview_dir).expanduser().resolve()
        pdir.mkdir(parents=True, exist_ok=True)
        b_path = pdir / f"{rom_info.game_code}_banner_256x128.png"
        i_path = pdir / f"{rom_info.game_code}_icon_48x48.png"
        b_path.write_bytes(encode_png_bytes(banner_img))
        i_path.write_bytes(encode_png_bytes(icon_48))
        preview_banner_png = str(b_path)
        preview_icon_png = str(i_path)

    sys_key = (rom_info.system or "gba").lower()
    if sys_key == "gba":
        # 3a. GBA: Native AGB_FIRM `.code` with 0x360-byte .CAA footer & empty 0x1000 RomFS
        code_bytes, rom_offset, padded_rom_size = build_gbavc_code_section(
            rom_bytes=rom_bytes,
            save_type_id=rom_info.save_type_id,
            ghosting=ghosting,
            dark_filter=dark_filter,
        )
        code_src = "native-agb-firm-0x360"
        exheader_bytes = build_gbavc_exheader(
            title_id=rom_info.title_id,
            save_data_size=rom_info.save_data_size,
            game_code=rom_info.game_code,
        )
        romfs_bytes = get_romfs_bytes()
    else:
        # 3b. Multi-Console (GB, GBC, NES, SNES, Genesis): Official 3DS VC or PicoDrive Forwarder
        tpl = get_console_template(sys_key)
        code_bytes = tpl["code"]
        rom_offset = 0
        padded_rom_size = len(rom_bytes)
        romfs_files: Dict[str, bytes] = dict(tpl["romfs_files"])

        if sys_key in ("gb", "gbc"):
            romfs_files["rom/rom.bin"] = rom_bytes
            code_src = f"official-3ds-{sys_key}-vc"
        elif sys_key == "nes":
            romfs_files["rom/rom.bin"] = convert_ines_to_tnes(rom_bytes)
            code_src = "official-3ds-nes-vc"
        elif sys_key == "snes":
            is_hirom = "HiROM" in rom_info.sdk_save_strings
            romfs_files["data.bin"] = build_snes_vc_data_bin(
                rom_bytes,
                tpl["extra"]["snes_data_hdr"],
                is_hirom=is_hirom,
            )
            romfs_files["KTR-UAEE.icn"] = smdh_bytes
            code_src = "official-3ds-snes-vc"
        elif sys_key == "md":
            code_bytes = patch_md_code_for_2x_rgui(code_bytes)
            slug = (
                "".join(c.lower() if c.isalnum() else "_" for c in metadata.short_title).strip("_")[:32]
                or "game"
            )
            uid_hex = f"{(rom_info.title_id >> 8) & 0xFFFFF:05x}"
            internal_name = f"gen_{uid_hex}_{slug}"
            if "retroarch.cfg" in romfs_files:
                cfg_bytes = romfs_files["retroarch.cfg"]
                cfg_bytes = cfg_bytes.replace(b"gen_100ee_mortal_kombat", internal_name.encode("ascii"))
                cfg_bytes = cfg_bytes.replace(b"nsui_formwarders_data", b"nsui_forwarders_data")
                cfg_bytes = cfg_bytes.replace(b'menu_linear_filter = "true"', b'menu_linear_filter = "false"')
                cfg_bytes = cfg_bytes.replace(b'menu_timedate_enable = "true"', b'menu_timedate_enable = "false"')
                cfg_bytes = cfg_bytes.replace(
                    b'menu_entry_normal_color = "ff999999"', b'menu_entry_normal_color = "ffeeeeee"'
                )
                cfg_bytes = cfg_bytes.replace(
                    b'menu_entry_hover_color = "ffffffff"', b'menu_entry_hover_color = "ffffd700"'
                )
                cfg_bytes = cfg_bytes.replace(
                    b'menu_title_color = "ffffffff"', b'menu_title_color = "ff00ffff"'
                )
                romfs_files["retroarch.cfg"] = cfg_bytes
            romfs_files["internal_name.txt"] = internal_name.encode("ascii")
            romfs_files["rom_path.txt"] = b"romfs:/rom.md"
            romfs_files["rom.md"] = rom_bytes
            code_src = "picodrive-3ds-forwarder"
        else:
            raise ValueError(f"Unsupported console system: {sys_key}")

        romfs_bytes = build_romfs(romfs_files)
        exheader_bytes = build_console_exheader(
            system=sys_key,
            title_id=rom_info.title_id,
            save_data_size=rom_info.save_data_size,
            template_bytes=tpl["exheader"],
        )

    # 4. Build ExeFS (.code, banner, icon, logo), NCCH (with RomFS), and CIA
    logo_bytes = get_logo_bytes()
    exefs_bytes = build_exefs(
        [
            (".code", code_bytes),
            ("banner", banner_bytes),
            ("icon", smdh_bytes),
            ("logo", logo_bytes),
        ]
    )

    ncch_bytes = build_ncch(
        title_id=rom_info.title_id,
        game_code=rom_info.game_code,
        exheader_bytes=exheader_bytes,
        exefs_bytes=exefs_bytes,
        romfs_bytes=romfs_bytes,
        maker_code=rom_info.maker_code,
        product_prefix=rom_info.product_prefix,
    )

    cia_bytes = build_cia(
        title_id=rom_info.title_id,
        ncch_bytes=ncch_bytes,
        exheader_bytes=exheader_bytes,
        smdh_bytes=smdh_bytes,
        save_data_size=rom_info.save_data_size,
    )

    out_p = Path(output_cia_path).expanduser().resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_bytes(cia_bytes)

    return CIABuildResult(
        output_cia_path=str(out_p),
        cia_size=len(cia_bytes),
        cia_sha256=hashlib.sha256(cia_bytes).hexdigest().upper(),
        rom_info=rom_info,
        metadata=metadata,
        rom_offset_in_code=rom_offset,
        padded_rom_size=padded_rom_size,
        ghosting=ghosting,
        dark_filter=dark_filter,
        banner_style=banner_style,
        code_stub_source=code_src,
        cgfx_header_source=cgfx_src,
        preview_banner_png_path=preview_banner_png,
        preview_icon_png_path=preview_icon_png,
    )


def verify_and_inspect_cia(cia_path: str) -> Dict[str, Any]:
    """
    Parse and cryptographically verify every layer of a 3DS GBA Virtual Console .cia file:
      - CIA Header, Retail Certificate Chain (MD5 & non-zero RSA keys)
      - Ticket & TMD TitleID, SaveDataSize, Content Chunk SHA-256, Content Info SHA-256, Superhash
      - NCCH Header, Product Code, Flags, ExHeader SHA-256, ExeFS Superblock SHA-256, RomFS Superblock SHA-256
      - ExHeader CoreVersion (0x202 = AGB_FIRM), SCI Flags (0x02), SaveDataSize
      - ExeFS files (.code, banner, icon, logo) and reverse-indexed SHA-256 hashes
      - 0x360-byte .CAA AGB_FIRM Footer & embedded GBA ROM header
      - SMDH titles & CBMD LZ11 CGFX + CWAV banner
    """
    data = Path(cia_path).expanduser().resolve().read_bytes()
    if len(data) < 0x3900:
        raise ValueError("File is too small to be a valid 3DS CIA archive")

    hdr_size, cia_type, cia_ver, cert_size, tik_size, tmd_size, meta_size, content_size = (
        struct.unpack_from("<IHHIIIIQ", data, 0x00)
    )
    if hdr_size != 0x2020:
        raise ValueError(f"Unexpected CIA header size: {hdr_size:#x}")

    off_cert = align_up(hdr_size, CIA_ALIGN)
    off_tik = off_cert + align_up(cert_size, CIA_ALIGN)
    off_tmd = off_tik + align_up(tik_size, CIA_ALIGN)
    off_content = off_tmd + align_up(tmd_size, CIA_ALIGN)
    off_meta = off_content + align_up(content_size, CIA_ALIGN)

    cert = data[off_cert : off_cert + cert_size]
    tik = data[off_tik : off_tik + tik_size]
    tmd = data[off_tmd : off_tmd + tmd_size]
    ncch = data[off_content : off_content + content_size]
    meta = data[off_meta : off_meta + meta_size] if meta_size > 0 else b""

    # 1. Verify Certificate Chain, Ticket & TMD
    cert_md5 = hashlib.md5(cert).hexdigest()
    cert_chain_ok = (cert_md5 == RETAIL_CERT_CHAIN_MD5) and any(tik[0x004:0x104]) and any(tmd[0x004:0x104])

    (tik_title_id,) = struct.unpack_from(">Q", tik, 0x1DC)
    (tmd_title_id,) = struct.unpack_from(">Q", tmd, 0x18C)
    (tmd_save_size,) = struct.unpack_from("<I", tmd, 0x19A)
    (chunk_size,) = struct.unpack_from(">Q", tmd, 0xB0C)
    chunk_sha = tmd[0xB14:0xB34]
    computed_ncch_sha = hashlib.sha256(ncch).digest()

    info_sha = tmd[0x208:0x228]
    computed_info_sha = hashlib.sha256(tmd[0xB04:0xB34]).digest()

    super_sha = tmd[0x1E4:0x204]
    computed_super_sha = hashlib.sha256(tmd[0x204:0xB04]).digest()

    # 2. Verify NCCH & ExHeader
    if ncch[0x100:0x104] != b"NCCH":
        raise ValueError("Invalid NCCH magic inside Content 0")
    (ncch_units,) = struct.unpack_from("<I", ncch, 0x104)
    (ncch_program_id,) = struct.unpack_from("<Q", ncch, 0x118)
    ncch_logo_sha = ncch[0x130:0x150]
    product_code = ncch[0x150:0x160].rstrip(b"\x00").decode("ascii", errors="replace")
    ncch_exh_sha = ncch[0x160:0x180]
    ncch_flags = list(ncch[0x188:0x190])
    logo_offset_mu, logo_size_mu = struct.unpack_from("<II", ncch, 0x198)
    exefs_offset_mu, exefs_size_mu, exefs_hash_mu = struct.unpack_from("<III", ncch, 0x1A0)
    romfs_offset_mu, romfs_size_mu, romfs_hash_mu = struct.unpack_from("<III", ncch, 0x1B0)
    ncch_exefs_super_sha = ncch[0x1C0:0x1E0]
    ncch_romfs_super_sha = ncch[0x1E0:0x200]

    logo_sha_ok = True
    if logo_offset_mu > 0 and logo_size_mu > 0:
        logo_slice = ncch[logo_offset_mu * MEDIA_UNIT : (logo_offset_mu + logo_size_mu) * MEDIA_UNIT]
        logo_sha_ok = ncch_logo_sha == hashlib.sha256(logo_slice).digest()

    exheader = ncch[0x200:0xA00]
    computed_exh_sha = hashlib.sha256(exheader[:0x400]).digest()

    process_name = exheader[0x000:0x008].rstrip(b"\x00").decode("ascii", errors="replace")
    sci_flags = exheader[0x00D]
    (exh_save_size,) = struct.unpack_from("<Q", exheader, 0x1C0)
    (exh_jump_id,) = struct.unpack_from("<Q", exheader, 0x1C8)
    (aci_program_id,) = struct.unpack_from("<Q", exheader, 0x200)
    (aci_core_version,) = struct.unpack_from("<I", exheader, 0x208)

    # 3. Verify ExeFS & RomFS
    exefs_off = exefs_offset_mu * MEDIA_UNIT
    exefs_len = exefs_size_mu * MEDIA_UNIT
    exefs = ncch[exefs_off : exefs_off + exefs_len]
    computed_exefs_super_sha = hashlib.sha256(exefs[: exefs_hash_mu * MEDIA_UNIT]).digest()

    romfs_hash_ok = True
    romfs_ivfc_ok = True
    romfs_files_summary: Dict[str, int] = {}
    if romfs_offset_mu > 0 and romfs_size_mu > 0:
        romfs_off = romfs_offset_mu * MEDIA_UNIT
        romfs_len = romfs_size_mu * MEDIA_UNIT
        romfs_hash_len = romfs_hash_mu * MEDIA_UNIT
        romfs_blob = ncch[romfs_off : romfs_off + romfs_len]
        romfs_hash_ok = ncch_romfs_super_sha == hashlib.sha256(romfs_blob[:romfs_hash_len]).digest()
        if len(romfs_blob) >= 0x4000:
            romfs_ivfc_ok = verify_romfs_ivfc(romfs_blob)
            try:
                extracted_romfs = extract_romfs(romfs_blob)
                romfs_files_summary = {k: len(v) for k, v in extracted_romfs.items()}
            except Exception:
                extracted_romfs = {}
        else:
            extracted_romfs = {}
    else:
        extracted_romfs = {}

    exefs_files: Dict[str, Dict[str, Any]] = {}
    raw_exefs_blobs: Dict[str, bytes] = {}
    for idx in range(10):
        entry = exefs[idx * 0x10 : (idx + 1) * 0x10]
        raw_name = entry[:8].rstrip(b"\x00")
        if not raw_name:
            continue
        fname = raw_name.decode("ascii", errors="replace")
        f_off, f_size = struct.unpack_from("<II", entry, 8)
        expected_f_sha = exefs[0x200 - (idx + 1) * 0x20 : 0x200 - idx * 0x20]
        f_data = exefs[0x200 + f_off : 0x200 + f_off + f_size]
        actual_f_sha = hashlib.sha256(f_data).digest()
        exefs_files[fname] = {
            "offset": f_off,
            "size": f_size,
            "sha256_ok": expected_f_sha == actual_f_sha,
        }
        raw_exefs_blobs[fname] = f_data

    # 4. Inspect .code & 0x360-byte .CAA footer + embedded GBA ROM
    caa_info: Dict[str, Any] = {}
    embedded_gba_info: Optional[GBARomInfo] = None
    if ".code" in raw_exefs_blobs:
        code_blob = raw_exefs_blobs[".code"]
        if len(code_blob) >= 0x360 and code_blob[-0x10:-0x0C] == b".CAA":
            footer = code_blob[-0x360:]
            _, cfg_rom_sz, save_id, _, _ = struct.unpack_from("<IIIHH", footer, 0x000)
            (ghosting,) = struct.unpack_from("<I", footer, 0x020)
            _, sec0_off, sec0_sz, _ = struct.unpack_from("<4I", footer, 0x330)
            magic, ver, tbl_off, sec_cnt = struct.unpack_from("<4sIII", footer, 0x350)
            caa_info = {
                "magic": magic.decode("ascii", errors="replace"),
                "magic_ok": magic == b".CAA",
                "version": ver,
                "rom_offset": sec0_off,
                "rom_size": sec0_sz,
                "save_type_id": save_id,
                "save_type_name": SAVE_TYPE_NAMES.get(save_id, f"Unknown ({save_id:#x})"),
                "ghosting": ghosting,
                "footer_size": 0x360,
            }
            if sec0_off + sec0_sz <= len(code_blob) - 0x360:
                gba_slice = code_blob[sec0_off : sec0_off + sec0_sz]
                try:
                    embedded_gba_info = parse_gba_rom("<embedded_in_cia>", rom_bytes=gba_slice)
                except Exception:
                    embedded_gba_info = None
        elif len(code_blob) >= 0x100 and code_blob[-0x100:-0xFC] == b".CAA":
            footer = code_blob[-0x100:]
            magic, ver, _, _, rom_off, rom_sz, save_id, _ = struct.unpack_from("<4sIIIIIII", footer, 0x00)
            caa_info = {
                "magic": magic.decode("ascii", errors="replace"),
                "magic_ok": magic == b".CAA",
                "version": ver,
                "rom_offset": rom_off,
                "rom_size": rom_sz,
                "save_type_id": save_id,
                "save_type_name": SAVE_TYPE_NAMES.get(save_id, f"Unknown ({save_id:#x})"),
                "ghosting": footer[0xFC],
                "footer_size": 0x100,
            }

    # 5. Inspect SMDH & CBMD Banner
    smdh_info: Dict[str, Any] = {}
    if "icon" in raw_exefs_blobs:
        smdh_info = parse_smdh(raw_exefs_blobs["icon"])

    banner_info: Dict[str, Any] = {}
    if "banner" in raw_exefs_blobs:
        b_blob = raw_exefs_blobs["banner"]
        if len(b_blob) >= 0x88 and b_blob[:4] == b"CBMD":
            offsets = struct.unpack_from("<14I", b_blob, 0x08)
            (cwav_off,) = struct.unpack_from("<I", b_blob, 0x84)
            bounds = sorted({o for o in offsets if o} | {cwav_off or len(b_blob)})
            end0 = next(b for b in bounds if b > offsets[0])
            comp_cgfx = b_blob[offsets[0] : end0]
            decomp_cgfx = decompress_lz11(comp_cgfx)
            cwav_blob = b_blob[cwav_off:] if cwav_off else b""
            banner_info = {
                "cbmd_ok": True,
                "cgfx_decompressed_size": len(decomp_cgfx),
                "cgfx_magic_ok": decomp_cgfx[:4] == b"CGFX",
                "cwav_size": len(cwav_blob),
                "cwav_magic_ok": cwav_blob[:4] == b"CWAV",
            }

    # Detect console system & embedded ROM for non-GBA systems
    detected_system = "gba"
    if aci_core_version == 0x00000202 or caa_info.get("magic_ok"):
        detected_system = "gba"
    elif "rom.md" in extracted_romfs:
        detected_system = "md"
        if embedded_gba_info is None:
            try:
                embedded_gba_info = parse_md_rom("<embedded_in_cia>", extracted_romfs["rom.md"])
            except Exception:
                pass
    elif "data.bin" in extracted_romfs:
        detected_system = "snes"
        if embedded_gba_info is None and len(extracted_romfs["data.bin"]) > 0x60:
            try:
                embedded_gba_info = parse_snes_rom("<embedded_in_cia>", extracted_romfs["data.bin"][0x60:])
            except Exception:
                pass
    elif "rom/rom.bin" in extracted_romfs:
        rom_bin = extracted_romfs["rom/rom.bin"]
        if rom_bin[:4] in (b"TNES", b"NES\x1a"):
            detected_system = "nes"
            try:
                embedded_gba_info = parse_nes_rom("<embedded_in_cia>", rom_bin)
            except Exception:
                pass
        else:
            detected_system = "gbc" if product_code.startswith("GBC-") or "dmg_cgb" in extracted_romfs else "gb"
            try:
                embedded_gba_info = parse_gb_rom("<embedded_in_cia>", rom_bin, system=detected_system)
            except Exception:
                pass

    all_hashes_ok = (
        cert_chain_ok
        and chunk_sha == computed_ncch_sha
        and info_sha == computed_info_sha
        and super_sha == computed_super_sha
        and ncch_exh_sha == computed_exh_sha
        and logo_sha_ok
        and ncch_exefs_super_sha == computed_exefs_super_sha
        and romfs_hash_ok
        and romfs_ivfc_ok
        and all(v["sha256_ok"] for v in exefs_files.values())
    )
    if detected_system == "gba":
        agb_firm_ready = (
            all_hashes_ok
            and aci_core_version == 0x00000202
            and (sci_flags & 0x03) == 0x02  # SDApplication=1, CompressExefsCode=0
            and tmd_save_size == exh_save_size
            and caa_info.get("magic_ok", False)
            and caa_info.get("rom_offset") == 0
            and "logo" in exefs_files
        )
    else:
        agb_firm_ready = (
            all_hashes_ok
            and aci_core_version == 0x00000002
            and tmd_save_size == exh_save_size
            and "logo" in exefs_files
            and len(romfs_files_summary) > 0
        )

    return {
        "file_path": str(Path(cia_path).expanduser().resolve()),
        "file_size": len(data),
        "system": detected_system,
        "cert_chain_valid": cert_chain_ok,
        "all_hashes_valid": all_hashes_ok,
        "romfs_ivfc_valid": romfs_ivfc_ok,
        "agb_firm_ready": agb_firm_ready,
        "title_id": f"{tmd_title_id:016X}",
        "ticket_title_id": f"{tik_title_id:016X}",
        "product_code": product_code,
        "process_name": process_name,
        "core_version": f"0x{aci_core_version:08X}",
        "sci_flags": f"0x{sci_flags:02X}",
        "save_data_size": tmd_save_size,
        "ncch_flags": [f"0x{b:02X}" for b in ncch_flags],
        "exefs_files": exefs_files,
        "romfs_files": romfs_files_summary,
        "caa_footer": caa_info,
        "smdh": smdh_info,
        "banner": banner_info,
        "embedded_gba": embedded_gba_info,
        "meta_size": len(meta),
    }


def extract_base_templates_from_cia(cia_path: str) -> Dict[str, str]:
    """
    Extract `exheader.bin`, `logo.bin`, `romfs.bin`, and `banner_3d_vc_cgfx.lz11` from an existing
    3DS GBA Virtual Console `.cia` file and save them into the local template cache directory.
    """
    data = Path(cia_path).expanduser().resolve().read_bytes()
    hdr_size, _, _, cert_size, tik_size, tmd_size, _, content_size = struct.unpack_from(
        "<IHHIIIIQ", data, 0x00
    )
    off_cert = align_up(hdr_size, CIA_ALIGN)
    off_tik = off_cert + align_up(cert_size, CIA_ALIGN)
    off_tmd = off_tik + align_up(tik_size, CIA_ALIGN)
    off_content = off_tmd + align_up(tmd_size, CIA_ALIGN)

    ncch = data[off_content : off_content + content_size]
    if ncch[0x100:0x104] != b"NCCH":
        raise ValueError("Invalid NCCH in source CIA")

    exheader = ncch[0x200:0xA00]
    exefs_offset_mu, exefs_size_mu = struct.unpack_from("<II", ncch, 0x1A0)
    romfs_offset_mu, romfs_size_mu = struct.unpack_from("<II", ncch, 0x1B0)
    exefs = ncch[exefs_offset_mu * MEDIA_UNIT : (exefs_offset_mu + exefs_size_mu) * MEDIA_UNIT]

    tpl_dir = get_templates_dir()
    saved: Dict[str, str] = {}

    exh_out = tpl_dir / "exheader.bin"
    exh_out.write_bytes(exheader)
    saved["exheader"] = str(exh_out)

    if romfs_offset_mu > 0 and romfs_size_mu > 0:
        romfs = ncch[romfs_offset_mu * MEDIA_UNIT : (romfs_offset_mu + romfs_size_mu) * MEDIA_UNIT]
        romfs_out = tpl_dir / "romfs.bin"
        romfs_out.write_bytes(romfs)
        saved["romfs"] = str(romfs_out)

    for idx in range(10):
        entry = exefs[idx * 0x10 : (idx + 1) * 0x10]
        fname = entry[:8].rstrip(b"\x00").decode("ascii", errors="replace")
        if not fname:
            continue
        f_off, f_size = struct.unpack_from("<II", entry, 8)
        f_data = exefs[0x200 + f_off : 0x200 + f_off + f_size]
        if fname == "logo":
            logo_out = tpl_dir / "logo.bin"
            logo_out.write_bytes(f_data)
            saved["logo"] = str(logo_out)
        elif fname == "banner" and f_data[:4] == b"CBMD":
            offsets = struct.unpack_from("<14I", f_data, 0x08)
            (cwav_off,) = struct.unpack_from("<I", f_data, 0x84)
            bounds = sorted({o for o in offsets if o} | {cwav_off or len(f_data)})
            end0 = next(b for b in bounds if b > offsets[0])
            comp_cgfx = f_data[offsets[0] : end0]
            bh_out = tpl_dir / "banner_3d_vc_cgfx.lz11"
            bh_out.write_bytes(comp_cgfx)
            saved["banner_3d_vc_cgfx"] = str(bh_out)

    return saved
