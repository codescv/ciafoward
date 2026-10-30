"""
Template Manager for 3DS GBA Virtual Console (AGB_FIRM) Binary Assets:
  - Authentic Retail Certificate Chain (`cert_chain`, 0xA00 bytes, MD5 `03de8229c72a836680d71a31436fdfb0`)
  - Signed Ticket & TMD Templates (`ticket`, 0x350 bytes; `tmd`, 0xB34 bytes)
  - NCCH Header & Signed GBA VC ExHeader Templates (`ncch_header`, 0x200 bytes; `exheader`, 0x800 bytes)
  - GBA VC ExeFS `logo` (0x2000 bytes) & `romfs` (0x1000 bytes)
  - AGB_FIRM `.CAA` Config Block Template (`config_block`, 0x324 bytes)
  - 2D (`cgfx_2d_header`, 0x1580 bytes) & 3D Virtual Console (`cgfx_3d_vc_lz11`) CGFX Banner Templates

All templates are bundled offline inside `ciafoward/assets/vc_templates.bin` (zlib-compressed).
"""

from __future__ import annotations

import json
import lzma
import struct
import zlib
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .metadata import _http_get_bytes, get_cache_dir

BANNER_CGFX_URLS = [
    "https://raw.githubusercontent.com/Steveice10/bannertool/master/resources/banner.cgfx",
    "https://raw.githubusercontent.com/carstene1ns/3ds-bannertool/master/resources/banner.cgfx",
]

_BUNDLE_CACHE: Optional[Dict[str, bytes]] = None
_CONSOLE_BUNDLE_CACHE: Optional[Tuple[Dict[str, Any], bytes]] = None


def _load_bundle() -> Dict[str, bytes]:
    """Load and decompress the bundled `vc_templates.bin` asset archive."""
    global _BUNDLE_CACHE
    if _BUNDLE_CACHE is not None:
        return _BUNDLE_CACHE

    asset_path = Path(__file__).resolve().parent / "assets" / "vc_templates.bin"
    if not asset_path.is_file():
        raise FileNotFoundError(f"Missing bundled VC template archive: {asset_path}")

    raw = zlib.decompress(asset_path.read_bytes())
    if raw[:4] != b"VCTP":
        raise ValueError("Corrupted vc_templates.bin header")

    (hdr_len,) = struct.unpack_from("<I", raw, 4)
    index = json.loads(raw[8 : 8 + hdr_len].decode("utf-8"))
    body_start = 8 + hdr_len
    out: Dict[str, bytes] = {
        key: raw[body_start + off : body_start + off + size]
        for key, (off, size) in index.items()
    }

    _BUNDLE_CACHE = out
    return out


def get_templates_dir() -> Path:
    d = get_cache_dir() / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cert_chain() -> bytes:
    """Return the authentic 0xA00-byte retail 3DS Certificate Chain (CA00000003 + XS0000000c + CP0000000b)."""
    return _load_bundle()["cert_chain"]


def get_ticket_template() -> bytes:
    """Return the 0x350-byte retail 3DS Ticket template."""
    return _load_bundle()["ticket"]


def get_tmd_template() -> bytes:
    """Return the 0xB34-byte retail 3DS Title Metadata (TMD) template."""
    return _load_bundle()["tmd"]


def get_ncch_header_template() -> bytes:
    """Return the 0x200-byte 3DS NCCH header template."""
    return _load_bundle()["ncch_header"]


def get_exheader_template() -> bytes:
    """Return the 0x800-byte 3DS GBA Virtual Console Extended Header template."""
    tpl_dir = get_templates_dir()
    cached = tpl_dir / "exheader.bin"
    if cached.is_file() and cached.stat().st_size == 0x800:
        return cached.read_bytes()
    return _load_bundle()["exheader"]


def get_logo_bytes() -> bytes:
    """Return the 0x2000-byte Nintendo 3DS / GBA VC ExeFS `logo` LZ11 binary."""
    tpl_dir = get_templates_dir()
    cached = tpl_dir / "logo.bin"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached.read_bytes()
    return _load_bundle()["logo"]


def get_romfs_bytes() -> bytes:
    """Return the 0x1000-byte minimal RomFS image required by 3DS GBA Virtual Console titles."""
    tpl_dir = get_templates_dir()
    cached = tpl_dir / "romfs.bin"
    if cached.is_file() and cached.stat().st_size >= 0x1000:
        return cached.read_bytes()
    return _load_bundle()["romfs"]


def get_config_block_template() -> bytes:
    """Return the 0x324-byte AGB_FIRM `.CAA` configuration & color LUT block template."""
    return _load_bundle()["config_block"]


def get_banner_3d_vc_cgfx_lz11() -> Tuple[bytes, str]:
    """Return the LZ11-compressed 3DS GBA Virtual Console 3D CGFX banner template."""
    tpl_dir = get_templates_dir()
    cached = tpl_dir / "banner_3d_vc_cgfx.lz11"
    if cached.is_file() and cached.stat().st_size > 0:
        return cached.read_bytes(), "cached-3d-vc-cgfx"
    return _load_bundle()["cgfx_3d_vc_lz11"], "bundled-3d-vc-cgfx"


def get_banner_cgfx_header(download: bool = True, timeout: float = 10.0) -> Tuple[bytes, str]:
    """
    Return the 0x1580-byte CGFX header from `bannertool`'s `banner.cgfx` (for 256x128 RGBA4 2D banners).
    Uses the bundled offline copy first, or downloads from GitHub if requested.
    """
    bundle = _load_bundle()
    if "cgfx_2d_header" in bundle and len(bundle["cgfx_2d_header"]) == 0x1580:
        return bundle["cgfx_2d_header"], "bundled-bannertool-0x1580"

    tpl_dir = get_templates_dir()
    cached_hdr = tpl_dir / "banner_header_0x1580.bin"
    if cached_hdr.is_file() and cached_hdr.stat().st_size == 0x1580:
        data = cached_hdr.read_bytes()
        if data[:4] == b"CGFX":
            return data, "cached-bannertool-0x1580"

    if download:
        for url in BANNER_CGFX_URLS:
            raw = _http_get_bytes(url, timeout=timeout)
            if raw and len(raw) >= 0x11580 and raw[:4] == b"CGFX":
                hdr_bytes = raw[:0x1580]
                try:
                    cached_hdr.write_bytes(hdr_bytes)
                except Exception:
                    pass
                return hdr_bytes, url

    raise RuntimeError("Could not load 2D CGFX banner header")


def build_gbavc_exheader(
    title_id: int,
    save_data_size: int = 0x00030000,
    game_code: str = "AGBA",
    template_bytes: Optional[bytes] = None,
) -> bytes:
    """
    Construct a 0x800-byte Extended Header (SCI + ACI + signed AccessDesc) for a 3DS GBA VC title
    using the authentic GBA VC ExHeader template:
      - `0x000..0x008`: Process name (4-char game code, e.g. `AWRE`)
      - `0x00D`: SCI Flags = `0x02` (`SDApplication=1`, `CompressExefsCode=0`)
      - `0x1C0`: Save Data Size (`<Q`, matching TMD SaveDataSize)
      - `0x1C8`: Jump ID (`<Q`, `title_id`)
      - `0x200`: ACI Program ID (`<Q`, `title_id`)
      - `0x208`: ACI CoreVersion (`<I`, `0x00000202` = `AGB_FIRM`)
      - Note: `0x400..0x800` (AccessDescriptor) is kept intact from the signed GBA VC template
        except `0x600` if unlocked, matching NSUI / official GBA VC behavior.
    """
    base = bytearray(template_bytes if (template_bytes and len(template_bytes) == 0x800) else get_exheader_template())
    proc_name = ("".join(c for c in game_code[:4].upper() if c.isalnum()) or "AGBA").encode("ascii")
    base[0x000:0x008] = proc_name[:8].ljust(8, b"\x00")
    base[0x00D] = 0x02  # Bit 1 = SDApplication (1), Bit 0 = CompressExefsCode (0)
    struct.pack_into("<Q", base, 0x1C0, save_data_size)
    struct.pack_into("<Q", base, 0x1C8, title_id)
    struct.pack_into("<Q", base, 0x200, title_id)
    struct.pack_into("<I", base, 0x208, 0x00000202)
    return bytes(base)


def _load_console_bundle() -> Tuple[Dict[str, Any], bytes]:
    """Load and decompress the deduplicated multi-console `console_templates.xz` archive."""
    global _CONSOLE_BUNDLE_CACHE
    if _CONSOLE_BUNDLE_CACHE is not None:
        return _CONSOLE_BUNDLE_CACHE

    asset_path = Path(__file__).resolve().parent / "assets" / "console_templates.xz"
    if not asset_path.is_file():
        raise FileNotFoundError(f"Missing bundled multi-console template archive: {asset_path}")

    raw = lzma.decompress(asset_path.read_bytes())
    if raw[:4] != b"VCMC":
        raise ValueError("Corrupted console_templates.xz header (expected 'VCMC')")

    (hdr_len,) = struct.unpack_from("<I", raw, 4)
    manifest = json.loads(raw[8 : 8 + hdr_len].decode("utf-8"))
    body = raw[8 + hdr_len :]
    _CONSOLE_BUNDLE_CACHE = (manifest, body)
    return _CONSOLE_BUNDLE_CACHE


def get_console_template(system: str) -> Dict[str, Any]:
    """
    Retrieve the bundled ExHeader, ExeFS `.code`, base RomFS files, and extra blobs
    for `system` in `("gb", "gbc", "nes", "snes", "md")`.
    """
    sys_key = system.lower()
    manifest, body = _load_console_bundle()
    systems = manifest.get("systems", {})
    if sys_key not in systems:
        raise ValueError(f"No console template found for system '{system}'")

    blobs = manifest["blobs"]
    sys_info = systems[sys_key]

    def _get_blob(h: str) -> bytes:
        off, length = blobs[h]
        return body[off : off + length]

    romfs_files: Dict[str, bytes] = {
        rel_path: _get_blob(h) for rel_path, h in sys_info.get("romfs", {}).items()
    }
    extra_blobs: Dict[str, bytes] = {
        k: _get_blob(h) for k, h in sys_info.get("extra", {}).items()
    }

    return {
        "exheader": _get_blob(sys_info["exheader"]),
        "code": _get_blob(sys_info["code"]),
        "romfs_files": romfs_files,
        "extra": extra_blobs,
    }


def get_console_banner_cgfx(system: str) -> Tuple[bytes, Optional[bytes]]:
    """
    Return the authentic 3D Virtual Console CGFX templates `(cgfx0, cgfx1)` for `system`:
      - `gba`, `nds`: `(decompress_lz11(cgfx_3d_vc_lz11), None)`
      - `gb`, `md`: `(banner_cgfx0, None)`
      - `gbc`, `nes`, `snes`: `(banner_cgfx0, banner_cgfx1)`
    """
    sys_key = (system or "gba").lower()
    if sys_key in ("gb", "gbc", "nes", "snes", "md"):
        manifest, body = _load_console_bundle()
        sys_info = manifest.get("systems", {}).get(sys_key, {})
        extra = sys_info.get("extra", {})
        blobs = manifest["blobs"]
        if "banner_cgfx0" in extra:
            off0, len0 = blobs[extra["banner_cgfx0"]]
            cgfx0 = body[off0 : off0 + len0]
            cgfx1: Optional[bytes] = None
            if "banner_cgfx1" in extra:
                off1, len1 = blobs[extra["banner_cgfx1"]]
                cgfx1 = body[off1 : off1 + len1]
            return cgfx0, cgfx1
    raise ValueError(f"No dedicated console banner CGFX for system '{system}'")


def build_console_exheader(
    system: str,
    title_id: int,
    save_data_size: int,
    template_bytes: Optional[bytes] = None,
) -> bytes:
    """
    Construct a 0x800-byte Extended Header for a non-GBA Virtual Console / Forwarder system
    (`gb`, `gbc`, `nes`, `snes`, `md`), preserving the engine's required process name (`QBAP` / `CtrApp`)
    and `SCI Flags`, while updating SaveDataSize (`0x1C0`), JumpID (`0x1C8`), ACI ProgramID (`0x200`),
    and AccessDescriptor ProgramID (`0x600`).
    """
    if template_bytes is None or len(template_bytes) != 0x800:
        template_bytes = get_console_template(system)["exheader"]
    base = bytearray(template_bytes)
    struct.pack_into("<Q", base, 0x1C0, save_data_size)
    struct.pack_into("<Q", base, 0x1C8, title_id)
    struct.pack_into("<Q", base, 0x200, title_id)
    struct.pack_into("<Q", base, 0x600, title_id)
    return bytes(base)


