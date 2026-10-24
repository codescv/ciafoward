"""
Unit and integration tests for `ciafoward`.
"""

import hashlib
import io
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from ciafoward.banner import (
    build_cbmd_banner,
    build_gba_chime_bcwav,
    build_smdh,
    compress_lz11,
    decode_rgb565_morton_icon,
    decompress_lz11,
    morton_index_8x8,
    parse_smdh,
)
from ciafoward.cia import (
    RETAIL_CERT_CHAIN_MD5,
    build_caa_footer,
    build_cert_chain,
    package_gba_to_cia,
    verify_and_inspect_cia,
)
from ciafoward.consoles import GB_LOGO_PREFIX, parse_rom_auto
from ciafoward.gba import (
    NINTENDO_LOGO_PREFIX,
    SaveTypeID,
    apply_ips_patch,
    compute_header_checksum,
    parse_gba_rom,
)
from ciafoward.graphics import (
    RGBAImage,
    compose_vc_banner_256x128,
    compose_vc_icons,
    decode_png_bytes,
    encode_png_bytes,
)
from ciafoward.metadata import resolve_game_metadata
from ciafoward.romfs import build_romfs, extract_romfs, verify_romfs_ivfc
from ciafoward.save_tool import convert_save_for_3ds, swap_eeprom_8byte_blocks
from ciafoward.templates import get_romfs_bytes


def make_synthetic_gba_rom(
    title: str = "ADVANCE WARS",
    game_code: str = "AWRE",
    maker_code: str = "01",
    version: int = 1,
    save_string: bytes = b"FLASH512_V130",
    with_rtc: bool = False,
    size: int = 256 * 1024,
) -> bytes:
    rom = bytearray(b"\xFF" * size)
    rom[0x00:0x04] = b"\x2E\x00\x00\xEA"
    rom[0x04 : 0x04 + len(NINTENDO_LOGO_PREFIX)] = NINTENDO_LOGO_PREFIX
    rom[0xA0:0xAC] = title.encode("ascii")[:12].ljust(12, b"\x00")
    rom[0xAC:0xB0] = game_code.encode("ascii")[:4].ljust(4, b" ")
    rom[0xB0:0xB2] = maker_code.encode("ascii")[:2].ljust(2, b"0")
    rom[0xB2] = 0x96
    rom[0xBC] = version & 0xFF
    rom[0xBD] = compute_header_checksum(rom)
    # Place SDK save string in ROM body
    rom[0x1000 : 0x1000 + len(save_string)] = save_string
    if with_rtc:
        rom[0x1200 : 0x1200 + 10] = b"SIIRTC_V001"
    return bytes(rom)


class TestCiaFoward(unittest.TestCase):
    def test_retail_cert_chain(self) -> None:
        certs = build_cert_chain()
        self.assertEqual(len(certs), 0xA00)
        self.assertEqual(hashlib.md5(certs).hexdigest(), RETAIL_CERT_CHAIN_MD5)

    def test_gba_header_and_save_detection(self) -> None:
        rom_bytes = make_synthetic_gba_rom(
            title="ADVANCE WARS",
            game_code="AWRE",
            maker_code="01",
            version=1,
            save_string=b"FLASH512_V130",
        )
        info = parse_gba_rom("Advance Wars (USA) (Rev 1).gba", rom_bytes=rom_bytes)
        self.assertTrue(info.header_valid)
        self.assertTrue(info.logo_valid)
        self.assertEqual(info.internal_title, "ADVANCE WARS")
        self.assertEqual(info.game_code, "AWRE")
        self.assertEqual(info.maker_code, "01")
        self.assertEqual(info.version, 1)
        self.assertEqual(info.padded_size, 1024 * 1024)
        self.assertEqual(info.save_type_id, SaveTypeID.FLASH_512K_PANASONIC)
        self.assertEqual(info.save_data_size, 0x00030000)

    def test_pokemon_emerald_flash1m_rtc_detection(self) -> None:
        rom_bytes = make_synthetic_gba_rom(
            title="POKEMON EMER",
            game_code="BPEE",
            maker_code="01",
            save_string=b"FLASH1M_V103",
            with_rtc=True,
        )
        info = parse_gba_rom("Pokemon - Emerald Version (USA, Europe).gba", rom_bytes=rom_bytes)
        self.assertTrue(info.has_rtc)
        self.assertEqual(info.save_type_id, SaveTypeID.FLASH_1M_MACRONIX_RTC)
        self.assertEqual(info.save_data_size, 0x00040400)

    def test_lz11_roundtrip(self) -> None:
        original = (b"CGFX-HEADER-TEST-1234567890" * 120) + bytes(range(256))
        compressed = compress_lz11(original)
        self.assertEqual(compressed[0], 0x11)
        decompressed = decompress_lz11(compressed)
        self.assertEqual(decompressed, original)

    def test_png_roundtrip_and_smdh(self) -> None:
        # Ensure Morton 8x8 covers all 64 tile indices without collision
        indices = {morton_index_8x8(x, y) for y in range(8) for x in range(8)}
        self.assertEqual(indices, set(range(64)))

        img = RGBAImage(48, 48, [((i * 5) & 0xF8, (i * 3) & 0xFC, (i * 7) & 0xF8, 255) for i in range(48 * 48)])
        png_data = encode_png_bytes(img)
        decoded = decode_png_bytes(png_data)
        self.assertEqual(decoded.width, 48)
        self.assertEqual(decoded.height, 48)
        self.assertEqual(decoded.pixels[0], img.pixels[0])

        icon24 = RGBAImage(24, 24, [((i * 9) & 0xF8, (i * 5) & 0xFC, (i * 3) & 0xF8, 255) for i in range(24 * 24)])
        smdh = build_smdh("Advance Wars", "Advance Wars\nReleased: 2001", "Nintendo", icon24, img)
        self.assertEqual(len(smdh), 0x36C0)
        parsed = parse_smdh(smdh)
        self.assertEqual(parsed["short_title"], "Advance Wars")
        self.assertEqual(parsed["publisher"], "Nintendo")
        self.assertEqual(parsed["region_lockout"], 0x7FFFFFFF)
        decoded_icon24 = decode_rgb565_morton_icon(smdh[0x2040:0x24C0], 24)
        self.assertEqual(decoded_icon24.width, 24)
        decoded_icon48 = decode_rgb565_morton_icon(smdh[0x24C0:0x36C0], 48)
        self.assertEqual(decoded_icon48.width, 48)
        # Check non-zero pixels across all quadrants of 8x8 tiles (e.g. x=6, y=6)
        self.assertNotEqual(decoded_icon48.pixels[6 * 48 + 6][:3], (0, 0, 0))

    def test_caa_footer_layout(self) -> None:
        footer = build_caa_footer(
            rom_size=0x400000,
            save_type=SaveTypeID.FLASH_512K_PANASONIC,
            ghosting=0x00,
            dark_filter=False,
            rom_offset=0,
        )
        self.assertEqual(len(footer), 0x360)
        self.assertEqual(footer[0x350:0x354], b".CAA")
        _, rom_sz, save_id, _, _ = struct.unpack_from("<IIIHH", footer, 0x000)
        (ghost_val,) = struct.unpack_from("<I", footer, 0x020)
        _, sec0_off, sec0_sz, _ = struct.unpack_from("<4I", footer, 0x330)
        self.assertEqual(rom_sz, 0x400000)
        self.assertEqual(save_id, SaveTypeID.FLASH_512K_PANASONIC)
        self.assertEqual(ghost_val, 0xFF)
        self.assertEqual(sec0_off, 0)
        self.assertEqual(sec0_sz, 0x400000)
        # Check linear color LUT endpoints (0 -> 0, 255 -> 255)
        self.assertEqual(footer[0x24:0x27], b"\x00\x00\x00")
        self.assertEqual(footer[0x321:0x324], b"\xFF\xFF\xFF")

    def test_ips_patch(self) -> None:
        base = bytearray(b"\x00" * 64)
        ips = (
            b"PATCH"
            + b"\x00\x00\x10\x00\x04ABCD"
            + b"\x00\x00\x20\x00\x00\x00\x05Z"
            + b"EOF"
        )
        patched = apply_ips_patch(bytes(base), ips)
        self.assertEqual(patched[0x10:0x14], b"ABCD")
        self.assertEqual(patched[0x20:0x25], b"ZZZZZ")

    def test_full_cia_build_and_verification(self) -> None:
        rom_bytes = make_synthetic_gba_rom()
        with tempfile.TemporaryDirectory() as tmpdir:
            gba_path = Path(tmpdir) / "Advance Wars (USA) (Rev 1).gba"
            cia_path = Path(tmpdir) / "Advance Wars.cia"
            gba_path.write_bytes(rom_bytes)

            rom_info = parse_gba_rom(str(gba_path), rom_bytes=rom_bytes)
            meta = resolve_game_metadata(rom_info, download_artwork=False, use_online_db=False)
            self.assertEqual(meta.short_title, "Advance Wars")
            self.assertEqual(meta.publisher, "Nintendo")

            res = package_gba_to_cia(
                rom_bytes=rom_bytes,
                rom_info=rom_info,
                metadata=meta,
                output_cia_path=str(cia_path),
                download_templates=False,
                save_preview_dir=tmpdir,
            )
            self.assertTrue(cia_path.is_file())
            self.assertGreater(res.cia_size, 256 * 1024)

            report = verify_and_inspect_cia(str(cia_path))
            self.assertTrue(report["cert_chain_valid"])
            self.assertTrue(report["all_hashes_valid"])
            self.assertTrue(report["agb_firm_ready"])
            self.assertEqual(report["core_version"], "0x00000202")
            self.assertEqual(report["sci_flags"], "0x02")
            self.assertEqual(report["product_code"], "CTR-P-AWRE")
            self.assertEqual(report["process_name"], "AWRE")
            self.assertEqual(report["smdh"]["short_title"], "Advance Wars")
            self.assertTrue(report["banner"]["cbmd_ok"])
            self.assertTrue(report["banner"]["cgfx_magic_ok"])
            self.assertTrue(report["banner"]["cwav_magic_ok"])
            self.assertIn("logo", report["exefs_files"])
            self.assertEqual(report["caa_footer"]["rom_offset"], 0)
            self.assertEqual(report["caa_footer"]["rom_size"], len(rom_bytes))
            self.assertEqual(report["embedded_gba"].game_code, "AWRE")

            # Test RetroArch 128KB .srm save conversion -> 64KB Flash 512K .sav
            srm_path = Path(tmpdir) / "Advance Wars.srm"
            out_sav = Path(tmpdir) / "AWRE_3ds.sav"
            srm_path.write_bytes(b"SAVE_HEADER_TEST" + b"\x00" * (128 * 1024 - 16))
            save_res = convert_save_for_3ds(str(cia_path), str(srm_path), str(out_sav))
            self.assertEqual(save_res.output_size, 64 * 1024)
            self.assertTrue(save_res.overpadding_stripped)
            self.assertTrue(out_sav.read_bytes().startswith(b"SAVE_HEADER_TEST"))

    def test_eeprom_byte_swap(self) -> None:
        raw = b"01234567ABCDEFGH"
        swapped = swap_eeprom_8byte_blocks(raw)
        self.assertEqual(swapped, b"76543210HGFEDCBA")
        self.assertEqual(swap_eeprom_8byte_blocks(swapped), raw)

    def test_romfs_ivfc_builder_and_verifier(self) -> None:
        empty_romfs = build_romfs({})
        self.assertEqual(empty_romfs, get_romfs_bytes())
        self.assertTrue(verify_romfs_ivfc(empty_romfs))

        sample_files = {
            "config.ini": b"[rom]\npath=rom/rom.bin\n",
            "rom/rom.bin": b"TEST_ROM_PAYLOAD" * 256,
            "patch/v1.patch": b"PATCH_DATA_12345",
        }
        built = build_romfs(sample_files)
        self.assertTrue(verify_romfs_ivfc(built))
        self.assertEqual(extract_romfs(built), sample_files)

    def test_multi_console_cia_builds(self) -> None:
        # 1. Synthetic GB / GBC ROM
        gb_rom = bytearray(32 * 1024)
        gb_rom[0x104 : 0x104 + len(GB_LOGO_PREFIX)] = GB_LOGO_PREFIX
        gb_rom[0x134:0x13F] = b"TETRIS     "
        gb_rom[0x147] = 0x03  # MBC1+RAM+BATTERY
        gb_rom[0x149] = 0x02  # 8KB SRAM
        chk = 0
        for b in gb_rom[0x134:0x14D]:
            chk = (chk - b - 1) & 0xFF
        gb_rom[0x14D] = chk

        gbc_rom = bytearray(gb_rom)
        gbc_rom[0x134:0x13F] = b"ZELDA DX   "
        gbc_rom[0x13F:0x143] = b"AZLE"
        gbc_rom[0x143] = 0xC0  # GBC-only
        chk_c = 0
        for b in gbc_rom[0x134:0x14D]:
            chk_c = (chk_c - b - 1) & 0xFF
        gbc_rom[0x14D] = chk_c

        # 2. Synthetic iNES (.nes) ROM (Mapper 1, 2x16KB PRG, 1x8KB CHR, Battery)
        nes_rom = bytearray(b"NES\x1a\x02\x01\x12\x00" + b"\x00" * 8) + bytearray(b"\xAA" * (32768 + 8192))

        # 3. Synthetic SNES LoROM (.sfc)
        snes_rom = bytearray(256 * 1024)
        snes_rom[0x7FC0 : 0x7FC0 + 21] = b"SUPER MARIO WORLD    "
        snes_rom[0x7FC0 + 0x15] = 0x20  # LoROM
        snes_rom[0x7FC0 + 0x18] = 0x01  # 2KB SRAM
        snes_rom[0x7FC0 + 0x19] = 0x01  # USA
        struct.pack_into("<HH", snes_rom, 0x7FC0 + 0x1C, 0xAAAA, 0x5555)

        # 4. Synthetic Sega Genesis (.md) ROM
        md_rom = bytearray(128 * 1024)
        md_rom[0x100:0x110] = b"SEGA GENESIS    "
        md_rom[0x120:0x150] = b"SONIC THE HEDGEHOG".ljust(0x30, b" ")
        md_rom[0x150:0x180] = b"SONIC THE HEDGEHOG".ljust(0x30, b" ")
        md_rom[0x180:0x18E] = b"GM 00001009-00"
        md_rom[0x1F0:0x1F3] = b"U  "

        cases = [
            ("Tetris (World).gb", bytes(gb_rom), "gb"),
            ("Legend of Zelda, The - Link's Awakening DX (USA).gbc", bytes(gbc_rom), "gbc"),
            ("Super Mario Bros. (World).nes", bytes(nes_rom), "nes"),
            ("Super Mario World (USA).sfc", bytes(snes_rom), "snes"),
            ("Sonic The Hedgehog (USA, Europe).md", bytes(md_rom), "md"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            for fname, raw_bytes, expected_sys in cases:
                rom_path = Path(tmpdir) / fname
                rom_path.write_bytes(raw_bytes)
                rom_info, clean_bytes = parse_rom_auto(str(rom_path))
                self.assertEqual(rom_info.system, expected_sys)

                meta = resolve_game_metadata(rom_info, download_artwork=False, use_online_db=False)
                out_cia = Path(tmpdir) / f"{expected_sys}_test.cia"
                package_gba_to_cia(
                    rom_bytes=clean_bytes,
                    rom_info=rom_info,
                    metadata=meta,
                    output_cia_path=str(out_cia),
                    download_templates=False,
                )
                report = verify_and_inspect_cia(str(out_cia))
                self.assertEqual(report["system"], expected_sys)
                self.assertTrue(report["cert_chain_valid"])
                self.assertTrue(report["all_hashes_valid"])
                self.assertTrue(report["romfs_ivfc_valid"])
                self.assertTrue(report["agb_firm_ready"])

            # Also test transparent .zip ROM unpacking
            zip_path = Path(tmpdir) / "Sonic_Bundle.zip"
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("Sonic The Hedgehog (USA).md", bytes(md_rom))
            zip_path.write_bytes(buf.getvalue())
            z_info, z_bytes = parse_rom_auto(str(zip_path))
            self.assertEqual(z_info.system, "md")
            self.assertEqual(len(z_bytes), len(md_rom))


if __name__ == "__main__":
    unittest.main()
