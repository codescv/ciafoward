"""
3DS SMDH Icon Builder, PICA200 Morton Z-Order Texture Encoder, Nintendo LZ11 Compressor/Decompressor,
BCWAV GBA Chime Synthesizer, and 2D / 3D Virtual Console CBMD Banner Packer.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, List, Optional, Sequence, Tuple

from .graphics import (
    CONSOLE_TEMPLATE_OVERRIDES,
    GENRE_TEMPLATES,
    RGB,
    RGBA,
    RGBAImage,
    TemplateTheme,
    draw_border,
    draw_text,
    fill_rect,
    resize_rgba,
)
from .templates import (
    get_banner_3d_vc_cgfx_lz11,
    get_banner_cgfx_header,
    get_console_banner_cgfx,
)

_MORTON_XY: List[Tuple[int, int]] = [
    (
        (i & 1) | ((i >> 1) & 2) | ((i >> 2) & 4),
        ((i >> 1) & 1) | ((i >> 2) & 2) | ((i >> 3) & 4),
    )
    for i in range(64)
]


def morton_index_8x8(x: int, y: int) -> int:
    """Compute the 3DS PICA200 Z-order (Morton) index (0..63) within an 8x8 pixel tile."""
    x = (x | (x << 2)) & 0x33
    x = (x | (x << 1)) & 0x55
    y = (y | (y << 2)) & 0x33
    y = (y | (y << 1)) & 0x55
    return x | (y << 1)


def encode_rgb565_morton_icon(rgb_pixels: Sequence[RGB], size: int) -> bytes:
    """
    Encode a (size x size) RGB image (where size is 24 or 48) into 3DS Morton-tiled RGB565 bytes.
    """
    if len(rgb_pixels) != size * size:
        raise ValueError(f"Expected {size * size} RGB pixels, got {len(rgb_pixels)}")

    out = bytearray()
    for ty in range(0, size, 8):
        for tx in range(0, size, 8):
            for x, y in _MORTON_XY:
                r, g, b = rgb_pixels[(ty + y) * size + (tx + x)]
                rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                out += struct.pack("<H", rgb565)
    return bytes(out)


def decode_rgb565_morton_icon(data: bytes, size: int) -> RGBAImage:
    """Decode a 3DS Morton-tiled RGB565 icon (24x24 or 48x48) back into an RGBAImage."""
    pixels: List[RGBA] = [(0, 0, 0, 255)] * (size * size)
    pos = 0
    for ty in range(0, size, 8):
        for tx in range(0, size, 8):
            for x, y in _MORTON_XY:
                (rgb565,) = struct.unpack_from("<H", data, pos)
                pos += 2
                r = ((rgb565 >> 11) & 0x1F) * 255 // 31
                g = ((rgb565 >> 5) & 0x3F) * 255 // 63
                b = (rgb565 & 0x1F) * 255 // 31
                pixels[(ty + y) * size + (tx + x)] = (r, g, b, 255)
    return RGBAImage(size, size, pixels)


def build_smdh(
    short_title: str,
    long_title: str,
    publisher: str,
    icon_24x24: RGBAImage,
    icon_48x48: RGBAImage,
    system: str = "gba",
    flags: Optional[int] = None,
) -> bytes:
    """
    Build a 0x36C0-byte (14,016-byte) 3DS SMDH icon & metadata file.
    Populates all 16 system language slots so the game title displays on any region 3DS.
    """
    if icon_24x24.width != 24 or icon_24x24.height != 24:
        raise ValueError("icon_24x24 must be 24x24")
    if icon_48x48.width != 48 or icon_48x48.height != 48:
        raise ValueError("icon_48x48 must be 48x48")

    smdh = bytearray(0x36C0)
    smdh[0x0000:0x0004] = b"SMDH"
    struct.pack_into("<HH", smdh, 0x0004, 0x0000, 0x0000)

    short_utf16 = short_title[:63].encode("utf-16le").ljust(0x80, b"\x00")[:0x80]
    long_utf16 = long_title[:127].encode("utf-16le").ljust(0x100, b"\x00")[:0x100]
    pub_utf16 = publisher[:63].encode("utf-16le").ljust(0x80, b"\x00")[:0x80]
    title_entry = short_utf16 + long_utf16 + pub_utf16  # 0x200 bytes

    for lang_idx in range(16):
        offset = 0x0008 + lang_idx * 0x200
        smdh[offset : offset + 0x200] = title_entry

    # Application Settings (0x2008 .. 0x203F)
    smdh[0x2008:0x2018] = b"\x00" * 16                   # Age ratings
    struct.pack_into("<I", smdh, 0x2018, 0x7FFFFFFF)     # Region Lockout: Region Free
    struct.pack_into("<I", smdh, 0x201C, 0x00000000)     # Match Maker ID
    struct.pack_into("<Q", smdh, 0x2020, 0x0000000000000000)
    if flags is None:
        sys_key = (system or "gba").lower()
        if sys_key == "snes":
            flags = 0x00001195
        elif sys_key in ("gb", "gbc", "nes"):
            flags = 0x00000195
        elif sys_key in ("md", "nds"):
            flags = 0x00000595
        else:
            flags = 0x00000425
    struct.pack_into("<I", smdh, 0x2028, flags)
    struct.pack_into("<H", smdh, 0x202C, 0x0000)         # EULA Version

    smdh[0x2040:0x24C0] = encode_rgb565_morton_icon(icon_24x24.to_rgb_list(), 24)
    smdh[0x24C0:0x36C0] = encode_rgb565_morton_icon(icon_48x48.to_rgb_list(), 48)
    return bytes(smdh)


def parse_smdh(smdh_bytes: bytes) -> dict:
    """Parse a 0x36C0-byte SMDH blob and return its titles, flags, and region lockout."""
    if len(smdh_bytes) < 0x36C0 or smdh_bytes[:4] != b"SMDH":
        raise ValueError("Invalid SMDH header or size")
    slot = smdh_bytes[0x0208:0x0408]
    short_title = slot[0x00:0x80].decode("utf-16le", errors="ignore").rstrip("\x00")
    long_title = slot[0x80:0x180].decode("utf-16le", errors="ignore").rstrip("\x00")
    publisher = slot[0x180:0x200].decode("utf-16le", errors="ignore").rstrip("\x00")
    (region_lockout,) = struct.unpack_from("<I", smdh_bytes, 0x2018)
    (flags,) = struct.unpack_from("<I", smdh_bytes, 0x2028)
    return {
        "short_title": short_title,
        "long_title": long_title,
        "publisher": publisher,
        "region_lockout": region_lockout,
        "flags": flags,
    }


def encode_banner_rgba4_morton(banner_img: RGBAImage) -> bytes:
    """
    Encode a 256x128 RGBAImage into the 0x10000-byte (65,536-byte) 3DS PICA200 RGBA4
    Morton-tiled texture buffer placed at offset 0x1580..0x11580 of `bannertool`'s CGFX.
    """
    if banner_img.width != 256 or banner_img.height != 128:
        banner_img = resize_rgba(banner_img, 256, 128)
    px = banner_img.pixels
    out = bytearray()
    for ty in range(0, 128, 8):
        for tx in range(0, 256, 8):
            for x, y in _MORTON_XY:
                r, g, b, a = px[(ty + y) * 256 + (tx + x)]
                val = ((r >> 4) << 12) | ((g >> 4) << 8) | ((b >> 4) << 4) | (a >> 4)
                out += struct.pack("<H", val)
    return bytes(out)


def encode_banner_rgba8_morton(banner_img: RGBAImage) -> bytes:
    """
    Encode an RGBAImage into PICA200 RGBA8 ([A, B, G, R]) Morton-tiled bytes.
    """
    w, h = banner_img.width, banner_img.height
    px = banner_img.pixels
    out = bytearray(w * h * 4)
    pos = 0
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for x, y in _MORTON_XY:
                r, g, b, a = px[(ty + y) * w + (tx + x)]
                out[pos] = a & 0xFF
                out[pos + 1] = b & 0xFF
                out[pos + 2] = g & 0xFF
                out[pos + 3] = r & 0xFF
                pos += 4
    return bytes(out)


def encode_la8_morton(img: RGBAImage) -> bytes:
    """
    Encode an RGBAImage into PICA200 LA8 ([Alpha, Luminance]) Morton-tiled bytes.
    """
    w, h = img.width, img.height
    px = img.pixels
    out = bytearray(w * h * 2)
    pos = 0
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for x, y in _MORTON_XY:
                r, g, b, a = px[(ty + y) * w + (tx + x)]
                lum = (r * 299 + g * 587 + b * 114) // 1000
                out[pos] = a & 0xFF
                out[pos + 1] = lum & 0xFF
                pos += 2
    return bytes(out)


def encode_rgb565_morton_image(img: RGBAImage) -> bytes:
    """
    Encode an arbitrary-dimension (multiples of 8) RGBAImage into PICA200 RGB565 Morton-tiled bytes.
    """
    w, h = img.width, img.height
    px = img.pixels
    out = bytearray(w * h * 2)
    pos = 0
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for x, y in _MORTON_XY:
                r, g, b, _ = px[(ty + y) * w + (tx + x)]
                rgb565 = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                struct.pack_into("<H", out, pos, rgb565)
                pos += 2
    return bytes(out)


def decode_rgb565_morton_image(data: bytes, w: int, h: int, offset: int = 0) -> RGBAImage:
    """Decode a PICA200 RGB565 Morton-tiled texture (w x h) into an RGBAImage."""
    img = RGBAImage(w, h)
    pos = offset
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for x, y in _MORTON_XY:
                (v,) = struct.unpack_from("<H", data, pos)
                pos += 2
                r = ((v >> 11) & 0x1F) * 255 // 31
                g = ((v >> 5) & 0x3F) * 255 // 63
                b = (v & 0x1F) * 255 // 31
                img.pixels[(ty + y) * w + (tx + x)] = (r, g, b, 255)
    return img


def decode_rgba8_morton_image(data: bytes, w: int, h: int, offset: int = 0) -> RGBAImage:
    """Decode a PICA200 RGBA8 ([A, B, G, R]) Morton-tiled texture (w x h) into an RGBAImage."""
    img = RGBAImage(w, h)
    pos = offset
    for ty in range(0, h, 8):
        for tx in range(0, w, 8):
            for x, y in _MORTON_XY:
                a, b, g, r = data[pos : pos + 4]
                pos += 4
                img.pixels[(ty + y) * w + (tx + x)] = (r, g, b, a)
    return img


def compress_lz11(
    data: bytes,
    literal_range: Optional[Tuple[int, int]] = None,
) -> bytes:
    """
    Fast Nintendo LZ11 (0x11) compressor using a 3-byte prefix hash chain (4 KiB window),
    padded to 4-byte alignment as required by 3DS CBMD banners.
    If `literal_range=(start, end)` is provided, bytes in `start..end` are emitted as raw
    literals and never matched by backreferences, enabling O(1) locale slot patching.
    """
    size = len(data)
    if size < (1 << 24):
        out = bytearray([0x11, size & 0xFF, (size >> 8) & 0xFF, (size >> 16) & 0xFF])
    else:
        out = bytearray([0x11, 0, 0, 0]) + struct.pack("<I", size)

    lit_s, lit_e = literal_range if literal_range is not None else (-1, -1)
    chains: Dict[bytes, List[int]] = {}
    pos = 0
    while pos < size:
        flag_index = len(out)
        out.append(0)
        flags = 0
        for bit in range(8):
            if pos >= size:
                break
            best_len, best_disp = 0, 0
            if not (lit_s <= pos < lit_e):
                key = data[pos : pos + 3]
                candidates = chains.get(key, ()) if len(key) == 3 else ()
                max_len = min(0x10110, size - pos)
                if pos < lit_s:
                    max_len = min(max_len, lit_s - pos)
                if max_len >= 3:
                    for cand in reversed(candidates[-48:]):
                        disp = pos - cand
                        if disp > 0x1000:
                            break
                        length = 3
                        while length < max_len and data[cand + length] == data[pos + length]:
                            length += 1
                        if length > best_len:
                            best_len, best_disp = length, disp
                            if length == max_len:
                                break
            step = best_len if best_len >= 3 else 1
            for p in range(pos, min(pos + step, size - 2)):
                if p + 3 <= lit_s or p >= lit_e:
                    chains.setdefault(data[p : p + 3], []).append(p)
            if best_len >= 3:
                flags |= 0x80 >> bit
                d = best_disp - 1
                if best_len <= 0x10:
                    out += bytes([((best_len - 1) << 4) | (d >> 8), d & 0xFF])
                elif best_len <= 0x110:
                    n = best_len - 0x11
                    out += bytes([n >> 4, ((n & 0xF) << 4) | (d >> 8), d & 0xFF])
                else:
                    n = best_len - 0x111
                    out += bytes([0x10 | (n >> 12), (n >> 4) & 0xFF, ((n & 0xF) << 4) | (d >> 8), d & 0xFF])
                pos += best_len
            else:
                out.append(data[pos])
                pos += 1
        out[flag_index] = flags
    rem = len(out) % 4
    if rem:
        out.extend(b"\x00" * (4 - rem))
    return bytes(out)


def decompress_lz11(comp: bytes) -> bytes:
    """Decompress Nintendo LZ11 (0x11) data for verification and inspection."""
    if not comp or comp[0] != 0x11:
        raise ValueError("Invalid LZ11 header byte (expected 0x11)")
    decomp_size = comp[1] | (comp[2] << 8) | (comp[3] << 16)
    pos = 4
    if decomp_size == 0 and len(comp) >= 8:
        (decomp_size,) = struct.unpack_from("<I", comp, 4)
        pos = 8

    out = bytearray()
    n = len(comp)
    while len(out) < decomp_size and pos < n:
        flags = comp[pos]
        pos += 1
        for bit in range(7, -1, -1):
            if len(out) >= decomp_size or pos >= n:
                break
            if (flags >> bit) & 1:
                b0 = comp[pos]
                pos += 1
                indicator = b0 >> 4
                if indicator == 0:
                    b1 = comp[pos]
                    b2 = comp[pos + 1]
                    pos += 2
                    length = (((b0 & 0x0F) << 4) | (b1 >> 4)) + 0x11
                    disp = (((b1 & 0x0F) << 8) | b2) + 1
                elif indicator == 1:
                    b1 = comp[pos]
                    b2 = comp[pos + 1]
                    b3 = comp[pos + 2]
                    pos += 3
                    length = (((b0 & 0x0F) << 12) | (b1 << 4) | (b2 >> 4)) + 0x111
                    disp = (((b2 & 0x0F) << 8) | b3) + 1
                else:
                    b1 = comp[pos]
                    pos += 1
                    length = indicator + 1
                    disp = (((b0 & 0x0F) << 8) | b1) + 1
                start = len(out) - disp
                for i in range(length):
                    out.append(out[start + i])
            else:
                out.append(comp[pos])
                pos += 1
    return bytes(out[:decomp_size])


def build_gba_chime_bcwav(
    sample_rate: int = 22050,
    duration_s: float = 1.1,
    system: str = "gba",
) -> bytes:
    """
    Synthesize a 3DS BCWAV (CWAV) audio file playing a console-specific boot chime:
      - `gba`: Classic GBA boot chime (B5 -> E6)
      - `gb` / `gbc`: Classic Game Boy 'ba-ding!' (C6 -> C7)
      - `nes`: 8-bit pulse wave chime (E5 -> B5 -> E6)
      - `snes`: Warm 16-bit triad chord (C5 + G5 + E6)
      - `md`: Sega Genesis FM-style fifth chord (G4 + D5 + B5)
    """
    sys_key = (system or "gba").lower()
    if sys_key in ("gb", "gbc"):
        f1, f2, split_t = 1046.50, 2093.00, 0.09
    elif sys_key == "nes":
        f1, f2, split_t = 987.77, 1318.51, 0.08
    elif sys_key == "snes":
        f1, f2, split_t = 659.25, 1318.51, 0.14
    elif sys_key == "md":
        f1, f2, split_t = 587.33, 880.00, 0.16
    elif sys_key == "nds":
        f1, f2, split_t = 783.99, 1174.66, 0.12
    else:
        f1, f2, split_t = 987.77, 1318.51, 0.11

    num_samples = int(sample_rate * duration_s)
    pcm = bytearray()
    for i in range(num_samples):
        t = i / sample_rate
        if t < split_t:
            env = math.exp(-t * 12.0)
            s = 0.65 * math.sin(2 * math.pi * f1 * t) + 0.35 * math.sin(2 * math.pi * (f1 * 2) * t)
        else:
            t2 = t - split_t
            env = math.exp(-t2 * 4.5)
            s = (
                0.60 * math.sin(2 * math.pi * f2 * t2)
                + 0.25 * math.sin(2 * math.pi * (f2 * 2) * t2)
                + 0.15 * math.sin(2 * math.pi * f1 * t) * math.exp(-t * 8.0)
            )
        val = max(-32767, min(32767, int(s * env * 22000)))
        pcm += struct.pack("<h", val)

    while len(pcm) % 0x20 != 0:
        pcm.append(0)

    info_size = 0x60
    data_size = 0x20 + len(pcm)
    total_size = 0x40 + info_size + data_size

    cwav_hdr = bytearray(0x40)
    cwav_hdr[0x00:0x04] = b"CWAV"
    struct.pack_into("<HHI", cwav_hdr, 0x04, 0xFEFF, 0x0040, 0x02010000)
    struct.pack_into("<I", cwav_hdr, 0x0C, total_size)
    struct.pack_into("<HH", cwav_hdr, 0x10, 2, 0)
    struct.pack_into("<HHII", cwav_hdr, 0x14, 0x7000, 0, 0x40, info_size)
    struct.pack_into("<HHII", cwav_hdr, 0x20, 0x7001, 0, 0x40 + info_size, data_size)

    info = bytearray(0x60)
    info[0x00:0x04] = b"INFO"
    struct.pack_into("<I", info, 0x04, info_size)
    info[0x08] = 0x01  # PCM16
    info[0x09] = 0x00  # One-shot
    struct.pack_into("<IIII", info, 0x0C, sample_rate, 0, num_samples, 0)
    struct.pack_into("<I", info, 0x1C, 1)  # 1 channel
    struct.pack_into("<HHI", info, 0x20, 0x7100, 0, 0x0C)
    struct.pack_into("<HHI", info, 0x28, 0x1F00, 0, 0x18)
    struct.pack_into("<HHI", info, 0x30, 0x0000, 0, 0xFFFFFFFF)

    data_blk = bytearray(0x20)
    data_blk[0x00:0x04] = b"DATA"
    struct.pack_into("<I", data_blk, 0x04, data_size)
    return bytes(cwav_hdr + info + data_blk + pcm)


def _encode_etc1_common3_body(
    dark_col: RGB,
    trim_col: RGB,
    body_col: RGB,
) -> bytes:
    """
    Encode the 128x128 (0x2000-byte) PICA200 ETC1 texture `COMMON3` for the 3D Virtual Console
    casing (`x=0..27` dark side/rear, `x=28..31` trim stripe, `x=32..127` main console shell).
    """
    out = bytearray(0x2000)
    pos = 0
    for ty in range(0, 128, 8):
        for tx in range(0, 128, 8):
            for sb_x, _sb_y in ((0, 0), (4, 0), (0, 4), (4, 4)):
                bx = tx + sb_x
                if bx < 28:
                    r, g, b = dark_col
                elif bx < 32:
                    r, g, b = trim_col
                else:
                    r, g, b = body_col
                r5 = max(0, min(31, (r * 31 + 127) // 255))
                g5 = max(0, min(31, (g * 31 + 127) // 255))
                b5 = max(0, min(31, (b * 31 + 127) // 255))
                high = (r5 << 27) | (g5 << 19) | (b5 << 11) | 0x02
                struct.pack_into("<II", out, pos, 0x00000000, high)
                pos += 8
    return bytes(out)


def _resolve_3d_theme(system: str = "gba", template: Optional[str] = None) -> TemplateTheme:
    if template and template.lower() not in ("auto", ""):
        tk = template.lower()
        if tk in GENRE_TEMPLATES:
            return GENRE_TEMPLATES[tk]
        if tk in CONSOLE_TEMPLATE_OVERRIDES:
            return CONSOLE_TEMPLATE_OVERRIDES[tk]
    sk = (system or "gba").lower()
    if sk != "gba" and sk in CONSOLE_TEMPLATE_OVERRIDES:
        return CONSOLE_TEMPLATE_OVERRIDES[sk]
    return GENRE_TEMPLATES["classic"]


_SNES_LOCALE_SLOTS: Tuple[bytes, ...] = (
    b"EUR_EN",
    b"EUR_FR",
    b"EUR_GE",
    b"EUR_IT",
    b"EUR_SP",
    b"EUR_DU",
    b"EUR_PO",
    b"EUR_RU",
    b"JPN_JP",
    b"USA_EN",
    b"USA_FR",
    b"USA_SP",
    b"USA_PO",
)


def _patch_title_plate_la8(
    cgfx: bytearray,
    offset: int,
    short_title: str,
    subtitle: str,
    is_nds: bool = False,
) -> None:
    """
    Decode a 256x64 PICA200 LA8 Virtual Console title plate at `offset`, erase its right-side
    title region (`x=94..247, y=4..59`), optionally customize the left emblem for `NDS`,
    render `short_title` and `subtitle`, and write the Morton-encoded LA8 bytes back in place.
    """
    plate_img = RGBAImage(256, 64)
    pos = offset
    for ty in range(0, 64, 8):
        for tx in range(0, 256, 8):
            for x, y in _MORTON_XY:
                a, lum = cgfx[pos : pos + 2]
                plate_img.pixels[(ty + y) * 256 + (tx + x)] = (lum, lum, lum, a)
                pos += 2

    # Erase text region (x=94..247, y=4..59) with plate's clean vertical gradient at x=93
    for y in range(4, 60):
        bg = plate_img.pixels[y * 256 + 93]
        for x in range(94, 248):
            plate_img.pixels[y * 256 + x] = bg

    if is_nds:
        # Replace left-side GBA emblem (x=10..92, y=6..57) with Nintendo DS Dual-Screen icon & text
        for y in range(6, 58):
            bg = plate_img.pixels[y * 256 + 93]
            for x in range(10, 93):
                plate_img.pixels[y * 256 + x] = bg
        # Draw dual-screen clamshell icon on left (x=16..38, y=14..48)
        fill_rect(plate_img, 16, 13, 24, 15, (55, 55, 55, 255))
        fill_rect(plate_img, 18, 15, 20, 11, (225, 225, 225, 255))
        fill_rect(plate_img, 15, 29, 26, 3, (90, 90, 90, 255))
        fill_rect(plate_img, 16, 33, 24, 15, (55, 55, 55, 255))
        fill_rect(plate_img, 18, 35, 20, 11, (205, 205, 205, 255))
        draw_text(plate_img, "NINTENDO", 44, 18, color=(45, 45, 45, 255), scale=1, shadow_color=None, max_width=48)
        draw_text(plate_img, "DS", 54, 31, color=(35, 35, 35, 255), scale=2, shadow_color=None, max_width=36)

    clean_title = short_title.strip() or "Virtual Console"
    if len(clean_title) <= 12:
        t_w = len(clean_title) * 12
        draw_text(
            plate_img,
            clean_title,
            max(96, 171 - t_w // 2),
            16,
            color=(36, 36, 36, 255),
            scale=2,
            shadow_color=None,
            max_width=148,
        )
    else:
        t_w = min(148, len(clean_title) * 6)
        draw_text(
            plate_img,
            clean_title[:24],
            max(96, 171 - t_w // 2),
            20,
            color=(36, 36, 36, 255),
            scale=1,
            shadow_color=None,
            max_width=148,
        )

    if subtitle:
        sub_str = subtitle.strip()[:24]
        s_w = min(148, len(sub_str) * 6)
        draw_text(
            plate_img,
            sub_str,
            max(96, 171 - s_w // 2),
            39,
            color=(75, 75, 75, 255),
            scale=1,
            shadow_color=None,
            max_width=148,
        )

    plate_bytes = encode_la8_morton(plate_img)
    cgfx[offset : offset + len(plate_bytes)] = plate_bytes


def _patch_rgba8_screen_mipmaps(
    cgfx: bytearray,
    offset: int,
    frame_img: RGBAImage,
    num_mips: int,
) -> None:
    """Encode `num_mips` mipmap levels (starting at 128x128) of `frame_img` as RGBA8 Morton."""
    mip_bytes = bytearray()
    w, h = 128, 128
    for _ in range(num_mips):
        level_img = frame_img if (w == 128 and h == 128) else resize_rgba(frame_img, w, h)
        mip_bytes.extend(encode_banner_rgba8_morton(level_img))
        w //= 2
        h //= 2
    cgfx[offset : offset + len(mip_bytes)] = mip_bytes


def _patch_rgb565_screen_mipmaps(
    cgfx: bytearray,
    offset: int,
    frame_img: RGBAImage,
    num_mips: int,
) -> None:
    """Encode `num_mips` mipmap levels (starting at 128x128) of `frame_img` as RGB565 Morton."""
    mip_bytes = bytearray()
    w, h = 128, 128
    for _ in range(num_mips):
        level_img = frame_img if (w == 128 and h == 128) else resize_rgba(frame_img, w, h)
        mip_bytes.extend(encode_rgb565_morton_image(level_img))
        w //= 2
        h //= 2
    cgfx[offset : offset + len(mip_bytes)] = mip_bytes


def build_3d_vc_cgfx_pair(
    screen_img: RGBAImage,
    short_title: str = "GBA Virtual Console",
    subtitle: str = "Nintendo",
    cgfx_3d_lz11: Optional[bytes] = None,
    system: str = "gba",
    template: Optional[str] = None,
) -> Tuple[bytes, Optional[bytes]]:
    """
    Build the platform-specific 3D Virtual Console CGFX pair `(cgfx0, cgfx1)`:
      - `gba`: Authentic 3D Game Boy Advance (`AGB-001`) console model (customizable shell theme)
      - `gb`: Authentic 3D Original Game Boy (`DMG-01`) console model with dot-matrix bezel
      - `gbc`: Authentic 3D Game Boy Color (`CGB-001`) console model with GBC lens
      - `nes`: Authentic 3D NES Front-Loader (`NES-001`) console + retro CRT TV set
      - `snes`: Authentic 3D Super Nintendo (`SNS-001`) console + controller + CRT TV set
      - `md`: Authentic 3D Sega Mega Drive / Genesis 16-BIT console + joypad + CRT TV set
      - `nds`: Custom 3D Nintendo DS (`NTR-001`) Platinum Silver & Graphite dual-screen handheld
    """
    sys_key = (system or "gba").lower()

    # --- Platform 1: Original Game Boy (DMG-01) ---
    if sys_key == "gb" and cgfx_3d_lz11 is None:
        raw0, _ = get_console_banner_cgfx("gb")
        cgfx0 = bytearray(raw0)
        # COMMON1 at 0x00B000 (128x128 RGBA8, 5 mips), inner screen box (24, 14)..(104, 86) -> 80x72
        frame_img = decode_rgba8_morton_image(cgfx0, 128, 128, 0x00B000)
        fit = resize_rgba(screen_img, 80, 72)
        for y in range(72):
            for x in range(80):
                sr, sg, sb, _ = fit.pixels[y * 80 + x]
                frame_img.pixels[(14 + y) * 128 + (24 + x)] = (sr, sg, sb, 255)
        _patch_rgba8_screen_mipmaps(cgfx0, 0x00B000, frame_img, num_mips=5)
        # COMMON2 at 0x020500 (256x64 LA8)
        _patch_title_plate_la8(cgfx0, 0x020500, short_title, subtitle)
        return bytes(cgfx0), None

    # --- Platform 2: Game Boy Color (CGB-001) ---
    if sys_key == "gbc" and cgfx_3d_lz11 is None:
        raw0, raw1 = get_console_banner_cgfx("gbc")
        cgfx0 = bytearray(raw0)
        cgfx1 = bytearray(raw1) if raw1 is not None else bytearray()
        # COMMON1 at 0x010780 (128x128 RGBA8, 5 mips), inner screen box (24, 29)..(104, 101) -> 80x72
        frame_img = decode_rgba8_morton_image(cgfx0, 128, 128, 0x010780)
        fit = resize_rgba(screen_img, 80, 72)
        for y in range(72):
            for x in range(80):
                sr, sg, sb, _ = fit.pixels[y * 80 + x]
                frame_img.pixels[(29 + y) * 128 + (24 + x)] = (sr, sg, sb, 255)
        _patch_rgba8_screen_mipmaps(cgfx0, 0x010780, frame_img, num_mips=5)
        # COMMON2 in cgfx1 at 0x000180 (256x64 LA8)
        if len(cgfx1) >= 0x8180:
            _patch_title_plate_la8(cgfx1, 0x000180, short_title, subtitle)
        return bytes(cgfx0), bytes(cgfx1) if cgfx1 else None

    # --- Platform 3: Nintendo Entertainment System (NES-001 + CRT TV) ---
    if sys_key == "nes" and cgfx_3d_lz11 is None:
        raw0, raw1 = get_console_banner_cgfx("nes")
        cgfx0 = bytearray(raw0)
        cgfx1 = bytearray(raw1) if raw1 is not None else bytearray()
        # COMMON1 at 0x00F180 (128x128 RGB565, 3 mips), inner CRT screen box (3, 13)..(125, 107) -> 122x94
        frame_img = decode_rgb565_morton_image(cgfx0, 128, 128, 0x00F180)
        fit = resize_rgba(screen_img, 122, 94)
        for y in range(94):
            for x in range(122):
                sr, sg, sb, _ = fit.pixels[y * 122 + x]
                frame_img.pixels[(13 + y) * 128 + (3 + x)] = (sr, sg, sb, 255)
        _patch_rgb565_screen_mipmaps(cgfx0, 0x00F180, frame_img, num_mips=3)
        # COMMON2 in cgfx1 at 0x000180 (256x64 LA8)
        if len(cgfx1) >= 0x8180:
            _patch_title_plate_la8(cgfx1, 0x000180, short_title, subtitle)
        return bytes(cgfx0), bytes(cgfx1) if cgfx1 else None

    # --- Platform 4: Super Nintendo (SNS-001 + Controller + CRT TV) ---
    if sys_key == "snes" and cgfx_3d_lz11 is None:
        raw0, raw1 = get_console_banner_cgfx("snes")
        cgfx0 = bytearray(raw0)
        cgfx1 = bytearray(raw1) if raw1 is not None else bytearray()
        # COMMON1 at 0x009F00 (128x128 RGBA8, 3 mips), inner CRT screen box (4, 6)..(124, 107) -> 120x101
        frame_img = decode_rgba8_morton_image(cgfx0, 128, 128, 0x009F00)
        fit = resize_rgba(screen_img, 120, 101)
        for y in range(101):
            for x in range(120):
                sr, sg, sb, _ = fit.pixels[y * 120 + x]
                frame_img.pixels[(6 + y) * 128 + (4 + x)] = (sr, sg, sb, 255)
        _patch_rgba8_screen_mipmaps(cgfx0, 0x009F00, frame_img, num_mips=3)
        # USA_PO2 in cgfx1 at 0x00DE00 (256x64 LA8)
        if len(cgfx1) >= 0x15E00:
            _patch_title_plate_la8(cgfx1, 0x00DE00, short_title, subtitle)
        return bytes(cgfx0), bytes(cgfx1) if cgfx1 else None

    # --- Platform 5: Sega Mega Drive / Genesis 16-BIT + Joypad + CRT TV ---
    if sys_key == "md" and cgfx_3d_lz11 is None:
        raw0, _ = get_console_banner_cgfx("md")
        cgfx0 = bytearray(raw0)
        # COMMON1 at 0x024B00 (256x64 LA8 title plate)
        _patch_title_plate_la8(cgfx0, 0x024B00, short_title, subtitle)
        # COMMON2 at 0x02CB00 (128x128 RGB565, 1 mip), inner CRT screen box (3, 5)..(125, 105) -> 122x100
        frame_img = decode_rgb565_morton_image(cgfx0, 128, 128, 0x02CB00)
        fit = resize_rgba(screen_img, 122, 100)
        for y in range(100):
            for x in range(122):
                sr, sg, sb, _ = fit.pixels[y * 122 + x]
                frame_img.pixels[(5 + y) * 128 + (3 + x)] = (sr, sg, sb, 255)
        _patch_rgb565_screen_mipmaps(cgfx0, 0x02CB00, frame_img, num_mips=1)
        return bytes(cgfx0), None

    # --- Platform 6 & 7: GBA (AGB-001) and NDS (Nintendo DS Dual-Screen Clamshell) ---
    if cgfx_3d_lz11 is None:
        cgfx_3d_lz11, _ = get_banner_3d_vc_cgfx_lz11()
    cgfx = bytearray(decompress_lz11(cgfx_3d_lz11))
    theme = _resolve_3d_theme(system=system, template=template)
    is_nds = sys_key == "nds"

    # 1. Decode COMMON1 (128x128 RGBA8 at 0x5880)
    frame_img = decode_rgba8_morton_image(cgfx, 128, 128, 0x5880)

    if is_nds:
        # Platinum Silver outer shell + Matte Graphite dual-screen clamshell bezel
        bm_r, bm_g, bm_b = (196, 204, 216)
    else:
        bm_r, bm_g, bm_b = theme.bezel_main

    for y in range(128):
        shade = 1.08 - 0.16 * (y / 127.0)
        sr_bezel = max(0, min(255, int(bm_r * shade)))
        sg_bezel = max(0, min(255, int(bm_g * shade)))
        sb_bezel = max(0, min(255, int(bm_b * shade)))
        for x in range(128):
            orig_r, orig_g, orig_b, orig_a = frame_img.pixels[y * 128 + x]
            if orig_a > 0 and (abs(orig_r - 126) + abs(orig_g - 100) + abs(orig_b - 197) <= 65):
                frame_img.pixels[y * 128 + x] = (sr_bezel, sg_bezel, sb_bezel, orig_a)

    if is_nds:
        # Draw Nintendo DS Clamshell Hinge Bar & Dual-Screen Surround inside (4, 18)..(124, 108)
        fill_rect(frame_img, 4, 19, 120, 6, (44, 48, 58, 255))
        fill_rect(frame_img, 4, 20, 120, 2, (140, 150, 165, 255))
        # Cylindrical hinge barrel ribs + microphone hole
        for hx in (18, 22, 104, 108):
            fill_rect(frame_img, hx, 19, 2, 6, (28, 32, 40, 255))
        fill_rect(frame_img, 63, 21, 2, 3, (15, 18, 24, 255))
        # Twin NDS status LEDs on right hinge (Green Power + Amber Wi-Fi)
        fill_rect(frame_img, 114, 20, 3, 4, (50, 235, 95, 255))
        fill_rect(frame_img, 119, 20, 3, 4, (255, 175, 40, 255))
        # Dark graphite touchscreen/display bezel surround inside V=[0.14..0.85] (y=19..106)
        fill_rect(frame_img, 5, 24, 118, 81, (32, 36, 44, 255))
        fit = resize_rgba(screen_img, 112, 66)
        for y in range(66):
            for x in range(112):
                sr, sg, sb, _ = fit.pixels[y * 112 + x]
                frame_img.pixels[(27 + y) * 128 + (8 + x)] = (sr, sg, sb, 255)
        draw_border(frame_img, 7, 26, 114, 68, (80, 90, 108, 255), thickness=1)
        # Bottom silver NINTENDO DS badge bar at y=95..103
        draw_text(
            frame_img,
            "NINTENDO DS",
            31,
            96,
            color=(225, 234, 248, 255),
            scale=1,
            shadow_color=None,
            max_width=68,
        )
    else:
        # Composite screen_img into inner GBA lens (6, 24) .. (122, 104) with bottom GAME BOY ADVANCE lens bar
        fill_rect(frame_img, 5, 24, 118, 80, (34, 38, 48, 255))
        fit = resize_rgba(screen_img, 112, 68)
        for y in range(68):
            for x in range(112):
                sr, sg, sb, _ = fit.pixels[y * 112 + x]
                frame_img.pixels[(26 + y) * 128 + (8 + x)] = (sr, sg, sb, 255)
        draw_border(frame_img, 7, 25, 114, 70, (64, 72, 90, 255), thickness=1)
        draw_text(
            frame_img,
            "GAME BOY ADVANCE",
            16,
            96,
            color=(210, 218, 236, 255),
            scale=1,
            shadow_color=None,
            max_width=96,
        )
        # Inner dark bezel rim + glowing power LED on right/left bezel
        draw_border(frame_img, 5, 24, 118, 80, (*theme.bezel_dark, 255), thickness=1)
        fill_rect(frame_img, 2, 58, 3, 4, theme.bezel_led)
        fill_rect(frame_img, 3, 59, 1, 2, (255, 255, 255, 230))

    _patch_rgba8_screen_mipmaps(cgfx, 0x5880, frame_img, num_mips=5)

    # 1b. Update COMMON3 (128x128 ETC1 at 0x22D80) so the 3D console shell matches the platform/theme
    common3_off = 0x22D80
    if common3_off + 0x2000 <= len(cgfx):
        if is_nds:
            cgfx[common3_off : common3_off + 0x2000] = _encode_etc1_common3_body(
                dark_col=(34, 38, 46),
                trim_col=(75, 175, 245),
                body_col=(196, 204, 216),
            )
        else:
            cgfx[common3_off : common3_off + 0x2000] = _encode_etc1_common3_body(
                dark_col=theme.bezel_dark,
                trim_col=theme.accent_primary[:3],
                body_col=theme.bezel_main,
            )

    # 2. Patch COMMON2 (256x64 LA8 at 0x1AD80)
    _patch_title_plate_la8(cgfx, 0x1AD80, short_title, subtitle, is_nds=is_nds)
    return bytes(cgfx), None


def build_3d_vc_cgfx(
    screen_img: RGBAImage,
    short_title: str = "GBA Virtual Console",
    subtitle: str = "Nintendo",
    cgfx_3d_lz11: Optional[bytes] = None,
    system: str = "gba",
    template: Optional[str] = None,
) -> bytes:
    """
    Build the primary uncompressed 3D Virtual Console CGFX (`cgfx0`) for `system`.
    """
    cgfx0, _ = build_3d_vc_cgfx_pair(
        screen_img=screen_img,
        short_title=short_title,
        subtitle=subtitle,
        cgfx_3d_lz11=cgfx_3d_lz11,
        system=system,
        template=template,
    )
    return cgfx0


def build_cbmd_banner(
    banner_img: RGBAImage,
    cgfx_header: Optional[bytes] = None,
    bcwav_bytes: Optional[bytes] = None,
    banner_style: str = "vc",
    screen_img: Optional[RGBAImage] = None,
    short_title: str = "GBA Virtual Console",
    subtitle: str = "Nintendo",
    system: str = "gba",
    template: Optional[str] = None,
) -> bytes:
    """
    Build a 3DS ExeFS `banner` (CBMD container):
      - If `banner_style in ("vc", "3d", "vc3d")`, uses the platform-specific 3D Virtual Console CGFX
        (`gba`, `gb`, `gbc`, `nes`, `snes`, `md`, `nds`), populating `CBMD` pointer slots `0` and `1..13`
        as required by each console's 3D scene structure.
      - Otherwise (or for `banner_style == "2d"`), uses `bannertool`'s 0x1580-byte CGFX header
        with a 256x128 RGBA4 Morton texture (`0x11580` bytes total).
    """
    sys_key = (system or "gba").lower()
    cgfx1_raw: Optional[bytes] = None
    if banner_style in ("vc", "3d", "vc3d"):
        src_screen = screen_img if screen_img is not None else banner_img
        cgfx_raw, cgfx1_raw = build_3d_vc_cgfx_pair(
            screen_img=src_screen,
            short_title=short_title,
            subtitle=subtitle,
            system=system,
            template=template,
        )
    else:
        if cgfx_header is None:
            cgfx_header, _ = get_banner_cgfx_header()
        if len(cgfx_header) == 0x1580:
            morton_tex = encode_banner_rgba4_morton(banner_img)
            cgfx_raw = cgfx_header + morton_tex
        else:
            morton_tex = encode_banner_rgba8_morton(banner_img)
            cgfx_raw = cgfx_header + morton_tex

    comp_cgfx0 = compress_lz11(cgfx_raw)

    if bcwav_bytes is None:
        bcwav_bytes = build_gba_chime_bcwav(system=system)

    cbmd_hdr = bytearray(0x88)
    cbmd_hdr[0x00:0x04] = b"CBMD"
    struct.pack_into("<I", cbmd_hdr, 0x04, 0x00000000)
    struct.pack_into("<I", cbmd_hdr, 0x08, 0x00000088)  # ptr[0] = 0x88

    body = bytearray(cbmd_hdr + comp_cgfx0)

    if cgfx1_raw is not None:
        if sys_key == "snes" and len(cgfx1_raw) >= 0x3200:
            # Compress once with 0x31f8..0x31fe forced as literal bytes so all 13 region/locale
            # slots (EUR_EN .. USA_PO) can be patched in O(1) without re-compressing 13 times.
            sentinel = b"Z9Q7X8"
            cgfx1_patched = bytearray(cgfx1_raw)
            cgfx1_patched[0x31F8:0x31FE] = sentinel
            comp1_base = compress_lz11(bytes(cgfx1_patched), literal_range=(0x31F8, 0x31FE))
            # Locate the 6 literal bytes of `sentinel` inside `comp1_base`
            lit_positions: List[int] = []
            # Walk LZ11 stream to find exact output offsets for decompressed 0x31F8..0x31FD
            d_pos = 0
            c_pos = 4 if (comp1_base[1] | comp1_base[2] | comp1_base[3]) != 0 else 8
            decomp_len = len(cgfx1_patched)
            while d_pos < decomp_len and c_pos < len(comp1_base):
                flags = comp1_base[c_pos]
                c_pos += 1
                for bit in range(7, -1, -1):
                    if d_pos >= decomp_len or c_pos >= len(comp1_base):
                        break
                    if (flags >> bit) & 1:
                        b0 = comp1_base[c_pos]
                        ind = b0 >> 4
                        if ind == 0:
                            b1 = comp1_base[c_pos + 1]
                            length = (((b0 & 0x0F) << 4) | (b1 >> 4)) + 0x11
                            c_pos += 3
                        elif ind == 1:
                            b1 = comp1_base[c_pos + 1]
                            b2 = comp1_base[c_pos + 2]
                            length = (((b0 & 0x0F) << 12) | (b1 << 4) | (b2 >> 4)) + 0x111
                            c_pos += 4
                        else:
                            length = ind + 1
                            c_pos += 2
                        d_pos += length
                    else:
                        if 0x31F8 <= d_pos < 0x31FE:
                            lit_positions.append(c_pos)
                        c_pos += 1
                        d_pos += 1

            for slot_idx, locale_code in enumerate(_SNES_LOCALE_SLOTS, start=1):
                slot_offset = len(body)
                struct.pack_into("<I", body, 0x08 + slot_idx * 4, slot_offset)
                slot_comp = bytearray(comp1_base)
                for b_idx, out_pos in enumerate(lit_positions):
                    slot_comp[out_pos] = locale_code[b_idx]
                body.extend(slot_comp)
        else:
            # GBC & NES: all 13 locale pointers point to the single compressed cgfx1 block
            comp_cgfx1 = compress_lz11(cgfx1_raw)
            slot_offset = len(body)
            for slot_idx in range(1, 14):
                struct.pack_into("<I", body, 0x08 + slot_idx * 4, slot_offset)
            body.extend(comp_cgfx1)

    while len(body) % 0x10 != 0:
        body.append(0)
    cwav_offset = len(body)
    struct.pack_into("<I", body, 0x84, cwav_offset)
    body.extend(bcwav_bytes)
    return bytes(body)
