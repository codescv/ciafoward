"""
3DS SMDH Icon Builder, PICA200 Morton Z-Order Texture Encoder, Nintendo LZ11 Compressor/Decompressor,
BCWAV GBA Chime Synthesizer, and 2D / 3D Virtual Console CBMD Banner Packer.
"""

from __future__ import annotations

import math
import struct
from typing import Dict, List, Optional, Sequence, Tuple

from .graphics import RGB, RGBA, RGBAImage, draw_text, resize_rgba
from .templates import get_banner_3d_vc_cgfx_lz11, get_banner_cgfx_header

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
    # Flags: 0x00000425 = Visible (0x01) | Allow 3D (0x04) | Record Usage (0x20) | Disable Save Data Backup (0x400)
    struct.pack_into("<I", smdh, 0x2028, 0x00000425)
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


def compress_lz11(data: bytes) -> bytes:
    """
    Fast Nintendo LZ11 (0x11) compressor using a 3-byte prefix hash chain (4 KiB window),
    padded to 4-byte alignment as required by 3DS CBMD banners.
    """
    size = len(data)
    if size < (1 << 24):
        out = bytearray([0x11, size & 0xFF, (size >> 8) & 0xFF, (size >> 16) & 0xFF])
    else:
        out = bytearray([0x11, 0, 0, 0]) + struct.pack("<I", size)

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
            key = data[pos : pos + 3]
            candidates = chains.get(key, ()) if len(key) == 3 else ()
            max_len = min(0x10110, size - pos)
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


def build_3d_vc_cgfx(
    screen_img: RGBAImage,
    short_title: str = "GBA Virtual Console",
    subtitle: str = "Nintendo",
    cgfx_3d_lz11: Optional[bytes] = None,
) -> bytes:
    """
    Patch the authentic 3D GBA Virtual Console `common` CGFX in-place:
      - `COMMON1` (128x128 RGBA8 + 4 mipmaps = 0x15500 bytes at 0x5880): GBA screen frame
      - `COMMON2` (256x64 LA8 = 0x8000 bytes at 0x1AD80): Virtual Console title plate
    Returns the uncompressed 172,416-byte CGFX buffer.
    """
    if cgfx_3d_lz11 is None:
        cgfx_3d_lz11, _ = get_banner_3d_vc_cgfx_lz11()
    cgfx = bytearray(decompress_lz11(cgfx_3d_lz11))

    # 1. Decode COMMON1 (128x128 RGBA8 at 0x5880)
    frame_img = RGBAImage(128, 128)
    pos = 0x5880
    for ty in range(0, 128, 8):
        for tx in range(0, 128, 8):
            for x, y in _MORTON_XY:
                a, b, g, r = cgfx[pos : pos + 4]
                frame_img.pixels[(ty + y) * 128 + (tx + x)] = (r, g, b, a)
                pos += 4

    # Composite screen_img into inner screen box (6, 25) .. (122, 103) -> 116x78
    fit = resize_rgba(screen_img, 116, 78)
    for y in range(78):
        for x in range(116):
            orig_r, orig_g, orig_b, _ = frame_img.pixels[(25 + y) * 128 + (6 + x)]
            if abs(orig_r - 126) + abs(orig_g - 100) + abs(orig_b - 197) > 45:
                sr, sg, sb, _ = fit.pixels[y * 116 + x]
                frame_img.pixels[(25 + y) * 128 + (6 + x)] = (sr, sg, sb, 255)

    # Encode all 5 mipmap levels of COMMON1 (128, 64, 32, 16, 8 -> 0x15500 bytes)
    mip_bytes = bytearray()
    w, h = 128, 128
    while w >= 8 and h >= 8:
        level_img = frame_img if (w == 128 and h == 128) else resize_rgba(frame_img, w, h)
        mip_bytes.extend(encode_banner_rgba8_morton(level_img))
        w //= 2
        h //= 2
    cgfx[0x5880 : 0x5880 + len(mip_bytes)] = mip_bytes

    # 2. Decode COMMON2 (256x64 LA8 at 0x1AD80)
    plate_img = RGBAImage(256, 64)
    pos = 0x1AD80
    for ty in range(0, 64, 8):
        for tx in range(0, 256, 8):
            for x, y in _MORTON_XY:
                a, lum = cgfx[pos : pos + 2]
                plate_img.pixels[(ty + y) * 256 + (tx + x)] = (lum, lum, lum, a)
                pos += 2

    # Erase text region (x=94..247, y=4..59) with plate's vertical gradient at x=96
    for y in range(4, 60):
        bg = plate_img.pixels[y * 256 + 96]
        for x in range(94, 248):
            plate_img.pixels[y * 256 + x] = bg

    # Render title and subtitle centered in x=96..246 (center x=171, max width=148)
    clean_title = short_title.strip() or "GBA Game"
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
    cgfx[0x1AD80 : 0x1AD80 + len(plate_bytes)] = plate_bytes
    return bytes(cgfx)


def build_cbmd_banner(
    banner_img: RGBAImage,
    cgfx_header: Optional[bytes] = None,
    bcwav_bytes: Optional[bytes] = None,
    banner_style: str = "vc",
    screen_img: Optional[RGBAImage] = None,
    short_title: str = "GBA Virtual Console",
    subtitle: str = "Nintendo",
    system: str = "gba",
) -> bytes:
    """
    Build a 3DS ExeFS `banner` (CBMD container):
      - If `banner_style in ("vc", "3d", "vc3d")`, uses the authentic 3D Virtual Console CGFX.
      - Otherwise (or for `banner_style == "2d"`), uses `bannertool`'s 0x1580-byte CGFX header
        with a 256x128 RGBA4 Morton texture (`0x11580` bytes total).
    """
    if banner_style in ("vc", "3d", "vc3d"):
        src_screen = screen_img if screen_img is not None else banner_img
        cgfx_raw = build_3d_vc_cgfx(
            screen_img=src_screen,
            short_title=short_title,
            subtitle=subtitle,
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

    comp_cgfx = compress_lz11(cgfx_raw)

    if bcwav_bytes is None:
        bcwav_bytes = build_gba_chime_bcwav(system=system)

    cbmd_hdr = bytearray(0x88)
    cbmd_hdr[0x00:0x04] = b"CBMD"
    struct.pack_into("<I", cbmd_hdr, 0x04, 0x00000000)
    struct.pack_into("<I", cbmd_hdr, 0x08, 0x00000088)  # Common CGFX offset = 0x88

    body = bytearray(cbmd_hdr + comp_cgfx)
    while len(body) % 0x10 != 0:
        body.append(0)
    cwav_offset = len(body)
    struct.pack_into("<I", body, 0x84, cwav_offset)
    body.extend(bcwav_bytes)
    return bytes(body)
