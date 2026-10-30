"""
Pure-Python Image Decoder/Encoder (PNG + optional Pillow/sips), Resizer, Bitmap Font Renderer,
and 3DS GBA Virtual Console Icon (48x48, 24x24) & Banner (256x128) Compositor.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .metadata import GENRE_LABELS, GameMetadata, detect_game_genre

RGBA = Tuple[int, int, int, int]
RGB = Tuple[int, int, int]

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Compact 5x7 uppercase/lowercase/digit/symbol bitmap font (each char is 5 columns x 7 rows, MSB top)
FONT_5X7: Dict[str, Tuple[int, int, int, int, int]] = {
    " ": (0x00, 0x00, 0x00, 0x00, 0x00),
    "!": (0x00, 0x00, 0x5F, 0x00, 0x00),
    '"': (0x00, 0x07, 0x00, 0x07, 0x00),
    "#": (0x14, 0x7F, 0x14, 0x7F, 0x14),
    "$": (0x24, 0x2A, 0x7F, 0x2A, 0x12),
    "%": (0x23, 0x13, 0x08, 0x64, 0x62),
    "&": (0x36, 0x49, 0x55, 0x22, 0x50),
    "'": (0x00, 0x05, 0x03, 0x00, 0x00),
    "(": (0x00, 0x1C, 0x22, 0x41, 0x00),
    ")": (0x00, 0x41, 0x22, 0x1C, 0x00),
    "*": (0x14, 0x08, 0x3E, 0x08, 0x14),
    "+": (0x08, 0x08, 0x3E, 0x08, 0x08),
    ",": (0x00, 0x50, 0x30, 0x00, 0x00),
    "-": (0x08, 0x08, 0x08, 0x08, 0x08),
    ".": (0x00, 0x60, 0x60, 0x00, 0x00),
    "/": (0x20, 0x10, 0x08, 0x04, 0x02),
    "0": (0x3E, 0x51, 0x49, 0x45, 0x3E),
    "1": (0x00, 0x42, 0x7F, 0x40, 0x00),
    "2": (0x42, 0x61, 0x51, 0x49, 0x46),
    "3": (0x21, 0x41, 0x45, 0x4B, 0x31),
    "4": (0x18, 0x14, 0x12, 0x7F, 0x10),
    "5": (0x27, 0x45, 0x45, 0x45, 0x39),
    "6": (0x3C, 0x4A, 0x49, 0x49, 0x30),
    "7": (0x01, 0x71, 0x09, 0x05, 0x03),
    "8": (0x36, 0x49, 0x49, 0x49, 0x36),
    "9": (0x06, 0x49, 0x49, 0x29, 0x1E),
    ":": (0x00, 0x36, 0x36, 0x00, 0x00),
    ";": (0x00, 0x56, 0x36, 0x00, 0x00),
    "<": (0x08, 0x14, 0x22, 0x41, 0x00),
    "=": (0x14, 0x14, 0x14, 0x14, 0x14),
    ">": (0x00, 0x41, 0x22, 0x14, 0x08),
    "?": (0x02, 0x01, 0x51, 0x09, 0x06),
    "@": (0x32, 0x49, 0x79, 0x41, 0x3E),
    "A": (0x7E, 0x11, 0x11, 0x11, 0x7E),
    "B": (0x7F, 0x49, 0x49, 0x49, 0x36),
    "C": (0x3E, 0x41, 0x41, 0x41, 0x22),
    "D": (0x7F, 0x41, 0x41, 0x22, 0x1C),
    "E": (0x7F, 0x49, 0x49, 0x49, 0x41),
    "F": (0x7F, 0x09, 0x09, 0x09, 0x01),
    "G": (0x3E, 0x41, 0x49, 0x49, 0x7A),
    "H": (0x7F, 0x08, 0x08, 0x08, 0x7F),
    "I": (0x00, 0x41, 0x7F, 0x41, 0x00),
    "J": (0x20, 0x40, 0x41, 0x3F, 0x01),
    "K": (0x7F, 0x08, 0x14, 0x22, 0x41),
    "L": (0x7F, 0x40, 0x40, 0x40, 0x40),
    "M": (0x7F, 0x02, 0x0C, 0x02, 0x7F),
    "N": (0x7F, 0x04, 0x08, 0x10, 0x7F),
    "O": (0x3E, 0x41, 0x41, 0x41, 0x3E),
    "P": (0x7F, 0x09, 0x09, 0x09, 0x06),
    "Q": (0x3E, 0x41, 0x51, 0x21, 0x5E),
    "R": (0x7F, 0x09, 0x19, 0x29, 0x46),
    "S": (0x46, 0x49, 0x49, 0x49, 0x31),
    "T": (0x01, 0x01, 0x7F, 0x01, 0x01),
    "U": (0x3F, 0x40, 0x40, 0x40, 0x3F),
    "V": (0x1F, 0x20, 0x40, 0x20, 0x1F),
    "W": (0x3F, 0x40, 0x38, 0x40, 0x3F),
    "X": (0x63, 0x14, 0x08, 0x14, 0x63),
    "Y": (0x07, 0x08, 0x70, 0x08, 0x07),
    "Z": (0x61, 0x51, 0x49, 0x45, 0x43),
    "[": (0x00, 0x7F, 0x41, 0x41, 0x00),
    "\\": (0x02, 0x04, 0x08, 0x10, 0x20),
    "]": (0x00, 0x41, 0x41, 0x7F, 0x00),
    "|": (0x00, 0x00, 0x7F, 0x00, 0x00),
    "_": (0x40, 0x40, 0x40, 0x40, 0x40),
    "a": (0x20, 0x54, 0x54, 0x54, 0x78),
    "b": (0x7F, 0x48, 0x44, 0x44, 0x38),
    "c": (0x38, 0x44, 0x44, 0x44, 0x20),
    "d": (0x38, 0x44, 0x44, 0x48, 0x7F),
    "e": (0x38, 0x54, 0x54, 0x54, 0x18),
    "f": (0x08, 0x7E, 0x09, 0x01, 0x02),
    "g": (0x0C, 0x52, 0x52, 0x52, 0x3E),
    "h": (0x7F, 0x08, 0x04, 0x04, 0x78),
    "i": (0x00, 0x44, 0x7D, 0x40, 0x00),
    "j": (0x20, 0x40, 0x44, 0x3D, 0x00),
    "k": (0x7F, 0x10, 0x28, 0x44, 0x00),
    "l": (0x00, 0x41, 0x7F, 0x40, 0x00),
    "m": (0x7C, 0x04, 0x18, 0x04, 0x78),
    "n": (0x7C, 0x08, 0x04, 0x04, 0x78),
    "o": (0x38, 0x44, 0x44, 0x44, 0x38),
    "p": (0x7C, 0x14, 0x14, 0x14, 0x08),
    "q": (0x08, 0x14, 0x14, 0x18, 0x7C),
    "r": (0x7C, 0x08, 0x04, 0x04, 0x08),
    "s": (0x48, 0x54, 0x54, 0x54, 0x20),
    "t": (0x04, 0x3F, 0x44, 0x40, 0x20),
    "u": (0x3C, 0x40, 0x40, 0x20, 0x7C),
    "v": (0x1C, 0x20, 0x40, 0x20, 0x1C),
    "w": (0x3C, 0x40, 0x30, 0x40, 0x3C),
    "x": (0x44, 0x28, 0x10, 0x28, 0x44),
    "y": (0x0C, 0x50, 0x50, 0x50, 0x3C),
    "z": (0x44, 0x64, 0x54, 0x4C, 0x44),
}


class RGBAImage:
    """Lightweight in-memory RGBA image buffer (width, height, flat list of RGBA tuples)."""

    def __init__(self, width: int, height: int, pixels: Optional[List[RGBA]] = None) -> None:
        self.width = width
        self.height = height
        if pixels is not None:
            if len(pixels) != width * height:
                raise ValueError(f"Expected {width * height} pixels, got {len(pixels)}")
            self.pixels = pixels
        else:
            self.pixels = [(0, 0, 0, 255)] * (width * height)

    def copy(self) -> "RGBAImage":
        return RGBAImage(self.width, self.height, list(self.pixels))

    def to_rgb_list(self, bg: RGB = (18, 20, 28)) -> List[RGB]:
        """Flatten RGBA pixels onto `bg` and return a list of (R, G, B) tuples."""
        br, bg_g, bb = bg
        out: List[RGB] = []
        for r, g, b, a in self.pixels:
            if a >= 255:
                out.append((r, g, b))
            elif a <= 0:
                out.append((br, bg_g, bb))
            else:
                inv = 255 - a
                out.append(
                    (
                        (r * a + br * inv) // 255,
                        (g * a + bg_g * inv) // 255,
                        (b * a + bb * inv) // 255,
                    )
                )
        return out


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def decode_png_bytes(data: bytes) -> RGBAImage:
    """
    Pure-Python PNG decoder supporting 8-bit/16-bit RGB, RGBA, Grayscale, Grayscale+Alpha,
    and 1/2/4/8-bit Indexed Palette (PLTE + tRNS), non-interlaced.
    Falls back to Pillow if installed and interlaced PNG is encountered.
    """
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("Not a valid PNG file (invalid signature)")

    pos = 8
    width = height = bit_depth = color_type = interlace = 0
    palette: List[RGB] = []
    trns_bytes = b""
    idat_chunks: List[bytes] = []

    while pos + 8 <= len(data):
        length, chunk_type = struct.unpack_from(">I4s", data, pos)
        pos += 8
        chunk_data = data[pos : pos + length]
        pos += length + 4  # skip CRC

        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filt, interlace = struct.unpack(
                ">IIBBBBB", chunk_data[:13]
            )
        elif chunk_type == b"PLTE":
            palette = [
                (chunk_data[i], chunk_data[i + 1], chunk_data[i + 2])
                for i in range(0, len(chunk_data) // 3 * 3, 3)
            ]
        elif chunk_type == b"tRNS":
            trns_bytes = chunk_data
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if width <= 0 or height <= 0:
        raise ValueError("Invalid PNG dimensions")

    if interlace != 0:
        # Try Pillow if Adam7 interlaced
        try:
            from PIL import Image
            import io

            im = Image.open(io.BytesIO(data)).convert("RGBA")
            return RGBAImage(im.width, im.height, list(im.getdata()))
        except Exception as exc:
            raise ValueError("Interlaced PNG requires Pillow or sips") from exc

    raw = zlib.decompress(b"".join(idat_chunks))

    # Determine bits per pixel & bytes per pixel for filtering
    samples_per_pixel = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bpp_bits = samples_per_pixel * bit_depth
    bpp_bytes = max(1, (bpp_bits + 7) // 8)
    stride = (width * bpp_bits + 7) // 8

    # Unfilter scanlines
    recon = bytearray(height * stride)
    in_pos = 0
    for y in range(height):
        filter_type = raw[in_pos]
        in_pos += 1
        row_start = y * stride
        prev_start = (y - 1) * stride
        for x in range(stride):
            val = raw[in_pos + x]
            a = recon[row_start + x - bpp_bytes] if x >= bpp_bytes else 0
            b = recon[prev_start + x] if y > 0 else 0
            c = recon[prev_start + x - bpp_bytes] if (y > 0 and x >= bpp_bytes) else 0

            if filter_type == 0:
                recon[row_start + x] = val
            elif filter_type == 1:
                recon[row_start + x] = (val + a) & 0xFF
            elif filter_type == 2:
                recon[row_start + x] = (val + b) & 0xFF
            elif filter_type == 3:
                recon[row_start + x] = (val + ((a + b) >> 1)) & 0xFF
            elif filter_type == 4:
                recon[row_start + x] = (val + _paeth_predictor(a, b, c)) & 0xFF
            else:
                raise ValueError(f"Unsupported PNG filter type: {filter_type}")
        in_pos += stride

    pixels: List[RGBA] = []
    for y in range(height):
        row = recon[y * stride : (y + 1) * stride]
        if color_type == 6 and bit_depth == 8:
            for x in range(width):
                off = x * 4
                pixels.append((row[off], row[off + 1], row[off + 2], row[off + 3]))
        elif color_type == 2 and bit_depth == 8:
            trns_rgb: Optional[RGB] = None
            if len(trns_bytes) >= 6:
                trns_rgb = (trns_bytes[1], trns_bytes[3], trns_bytes[5])
            for x in range(width):
                off = x * 3
                r, g, b = row[off], row[off + 1], row[off + 2]
                a = 0 if (trns_rgb and (r, g, b) == trns_rgb) else 255
                pixels.append((r, g, b, a))
        elif color_type == 3:
            # Indexed palette (bit_depth 1, 2, 4, or 8)
            for x in range(width):
                if bit_depth == 8:
                    idx = row[x]
                elif bit_depth == 4:
                    byte_val = row[x >> 1]
                    idx = (byte_val >> 4) if (x & 1) == 0 else (byte_val & 0x0F)
                elif bit_depth == 2:
                    byte_val = row[x >> 2]
                    shift = 6 - 2 * (x & 3)
                    idx = (byte_val >> shift) & 0x03
                elif bit_depth == 1:
                    byte_val = row[x >> 3]
                    shift = 7 - (x & 7)
                    idx = (byte_val >> shift) & 0x01
                else:
                    idx = 0
                r, g, b = palette[idx] if idx < len(palette) else (0, 0, 0)
                a = trns_bytes[idx] if idx < len(trns_bytes) else 255
                pixels.append((r, g, b, a))
        elif color_type == 0:
            # Grayscale
            for x in range(width):
                if bit_depth == 8:
                    g = row[x]
                elif bit_depth == 16:
                    g = row[x * 2]
                elif bit_depth == 4:
                    byte_val = row[x >> 1]
                    v = (byte_val >> 4) if (x & 1) == 0 else (byte_val & 0x0F)
                    g = v * 17
                elif bit_depth == 2:
                    byte_val = row[x >> 2]
                    v = (byte_val >> (6 - 2 * (x & 3))) & 0x03
                    g = v * 85
                elif bit_depth == 1:
                    byte_val = row[x >> 3]
                    v = (byte_val >> (7 - (x & 7))) & 0x01
                    g = v * 255
                else:
                    g = 0
                pixels.append((g, g, g, 255))
        elif color_type == 4 and bit_depth == 8:
            for x in range(width):
                g, a = row[x * 2], row[x * 2 + 1]
                pixels.append((g, g, g, a))
        elif bit_depth == 16 and color_type in (2, 6):
            step = 6 if color_type == 2 else 8
            for x in range(width):
                off = x * step
                r, g, b = row[off], row[off + 2], row[off + 4]
                a = row[off + 6] if color_type == 6 else 255
                pixels.append((r, g, b, a))
        else:
            raise ValueError(f"Unsupported PNG color_type={color_type}, bit_depth={bit_depth}")

    return RGBAImage(width, height, pixels)


def encode_png_bytes(img: RGBAImage) -> bytes:
    """Encode an RGBAImage into standard 8-bit RGBA PNG bytes."""

    def make_chunk(chunk_type: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", img.width, img.height, 8, 6, 0, 0, 0)
    raw_rows = bytearray()
    w = img.width
    for y in range(img.height):
        raw_rows.append(0)  # Filter 0 (None)
        row_slice = img.pixels[y * w : (y + 1) * w]
        for r, g, b, a in row_slice:
            raw_rows.extend((r & 0xFF, g & 0xFF, b & 0xFF, a & 0xFF))

    compressed = zlib.compress(bytes(raw_rows), level=6)
    return (
        PNG_SIGNATURE
        + make_chunk(b"IHDR", ihdr)
        + make_chunk(b"IDAT", compressed)
        + make_chunk(b"IEND", b"")
    )


def load_image_file(file_path: str) -> RGBAImage:
    """
    Load an image file (PNG, or JPEG/BMP/GIF via Pillow or macOS sips) into an RGBAImage.
    """
    p = Path(file_path).expanduser().resolve()
    data = p.read_bytes()

    if data.startswith(PNG_SIGNATURE):
        try:
            return decode_png_bytes(data)
        except Exception:
            pass

    # Fallback 1: Pillow if installed
    try:
        from PIL import Image
        import io

        im = Image.open(io.BytesIO(data)).convert("RGBA")
        return RGBAImage(im.width, im.height, list(im.getdata()))
    except Exception:
        pass

    # Fallback 2: macOS /usr/bin/sips conversion to temporary PNG
    if os.path.exists("/usr/bin/sips"):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            subprocess.run(
                ["/usr/bin/sips", "-s", "format", "png", str(p), "--out", tmp_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return decode_png_bytes(Path(tmp_path).read_bytes())
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    raise ValueError(f"Unsupported image format or corrupted PNG: {file_path}")


def resize_rgba(img: RGBAImage, target_w: int, target_h: int) -> RGBAImage:
    """
    High-quality area-averaging (for downscaling) + bilinear (for upscaling) RGBA image resizer.
    """
    if img.width == target_w and img.height == target_h:
        return img.copy()

    src_w, src_h = img.width, img.height
    src = img.pixels
    out: List[RGBA] = [(0, 0, 0, 255)] * (target_w * target_h)

    scale_x = src_w / target_w
    scale_y = src_h / target_h

    if scale_x >= 1.0 and scale_y >= 1.0:
        # Box area averaging for crisp downsampling (prevents aliasing on 24x24 and 48x48 icons)
        for dy in range(target_h):
            sy0 = dy * scale_y
            sy1 = (dy + 1) * scale_y
            iy0 = int(sy0)
            iy1 = min(src_h - 1, int(math.ceil(sy1)) - 1)
            for dx in range(target_w):
                sx0 = dx * scale_x
                sx1 = (dx + 1) * scale_x
                ix0 = int(sx0)
                ix1 = min(src_w - 1, int(math.ceil(sx1)) - 1)

                r_acc = g_acc = b_acc = a_acc = count = 0
                for iy in range(iy0, iy1 + 1):
                    row_off = iy * src_w
                    for ix in range(ix0, ix1 + 1):
                        r, g, b, a = src[row_off + ix]
                        r_acc += r
                        g_acc += g
                        b_acc += b
                        a_acc += a
                        count += 1
                if count > 0:
                    out[dy * target_w + dx] = (
                        r_acc // count,
                        g_acc // count,
                        b_acc // count,
                        a_acc // count,
                    )
    else:
        # Bilinear interpolation
        for dy in range(target_h):
            fy = (dy + 0.5) * scale_y - 0.5
            iy = max(0, min(src_h - 1, int(math.floor(fy))))
            iy_next = min(src_h - 1, iy + 1)
            wy = max(0.0, min(1.0, fy - iy))
            for dx in range(target_w):
                fx = (dx + 0.5) * scale_x - 0.5
                ix = max(0, min(src_w - 1, int(math.floor(fx))))
                ix_next = min(src_w - 1, ix + 1)
                wx = max(0.0, min(1.0, fx - ix))

                p00 = src[iy * src_w + ix]
                p10 = src[iy * src_w + ix_next]
                p01 = src[iy_next * src_w + ix]
                p11 = src[iy_next * src_w + ix_next]

                w00 = (1.0 - wx) * (1.0 - wy)
                w10 = wx * (1.0 - wy)
                w01 = (1.0 - wx) * wy
                w11 = wx * wy

                r = int(p00[0] * w00 + p10[0] * w10 + p01[0] * w01 + p11[0] * w11)
                g = int(p00[1] * w00 + p10[1] * w10 + p01[1] * w01 + p11[1] * w11)
                b = int(p00[2] * w00 + p10[2] * w10 + p01[2] * w01 + p11[2] * w11)
                a = int(p00[3] * w00 + p10[3] * w10 + p01[3] * w01 + p11[3] * w11)
                out[dy * target_w + dx] = (
                    max(0, min(255, r)),
                    max(0, min(255, g)),
                    max(0, min(255, b)),
                    max(0, min(255, a)),
                )

    return RGBAImage(target_w, target_h, out)


def fill_rect(
    img: RGBAImage,
    x0: int,
    y0: int,
    w: int,
    h: int,
    color: RGBA,
) -> None:
    """Alpha-blend a filled rectangle onto `img`."""
    cr, cg, cb, ca = color
    if ca <= 0:
        return
    x1 = min(img.width, x0 + w)
    y1 = min(img.height, y0 + h)
    x0 = max(0, x0)
    y0 = max(0, y0)
    for y in range(y0, y1):
        row = y * img.width
        for x in range(x0, x1):
            if ca >= 255:
                img.pixels[row + x] = (cr, cg, cb, 255)
            else:
                dr, dg, db, da = img.pixels[row + x]
                inv = 255 - ca
                img.pixels[row + x] = (
                    (cr * ca + dr * inv) // 255,
                    (cg * ca + dg * inv) // 255,
                    (cb * ca + db * inv) // 255,
                    max(da, ca),
                )


def draw_border(
    img: RGBAImage,
    x0: int,
    y0: int,
    w: int,
    h: int,
    color: RGBA,
    thickness: int = 1,
) -> None:
    """Draw an outline border rectangle."""
    fill_rect(img, x0, y0, w, thickness, color)
    fill_rect(img, x0, y0 + h - thickness, w, thickness, color)
    fill_rect(img, x0, y0, thickness, h, color)
    fill_rect(img, x0 + w - thickness, y0, thickness, h, color)


def blit_image(dst: RGBAImage, src: RGBAImage, dx: int, dy: int) -> None:
    """Alpha-composite `src` onto `dst` at `(dx, dy)`."""
    for sy in range(src.height):
        ty = dy + sy
        if ty < 0 or ty >= dst.height:
            continue
        dst_row = ty * dst.width
        src_row = sy * src.width
        for sx in range(src.width):
            tx = dx + sx
            if tx < 0 or tx >= dst.width:
                continue
            sr, sg, sb, sa = src.pixels[src_row + sx]
            if sa >= 255:
                dst.pixels[dst_row + tx] = (sr, sg, sb, 255)
            elif sa > 0:
                dr, dg, db, da = dst.pixels[dst_row + tx]
                inv = 255 - sa
                dst.pixels[dst_row + tx] = (
                    (sr * sa + dr * inv) // 255,
                    (sg * sa + dg * inv) // 255,
                    (sb * sa + db * inv) // 255,
                    max(da, sa),
                )


def draw_text(
    img: RGBAImage,
    text: str,
    x: int,
    y: int,
    color: RGBA = (255, 255, 255, 255),
    scale: int = 1,
    shadow_color: Optional[RGBA] = (0, 0, 0, 180),
    max_width: Optional[int] = None,
) -> int:
    """
    Render text using the built-in 5x7 bitmap font at `(x, y)` with optional drop shadow.
    Returns the total pixel width drawn.
    """
    char_advance = 6 * scale
    cur_x = x
    for ch in text:
        if max_width is not None and (cur_x + 5 * scale - x) > max_width:
            break
        glyph = FONT_5X7.get(ch) or FONT_5X7.get(ch.upper()) or FONT_5X7["?"]
        for col_idx, col_bits in enumerate(glyph):
            for row_idx in range(7):
                if (col_bits >> row_idx) & 1:
                    px = cur_x + col_idx * scale
                    py = y + row_idx * scale
                    if shadow_color:
                        fill_rect(img, px + scale, py + scale, scale, scale, shadow_color)
                    fill_rect(img, px, py, scale, scale, color)
        cur_x += char_advance
    return cur_x - x


CONSOLE_THEMES: Dict[str, Tuple[str, str, RGB, RGB, RGBA]] = {
    "gba": ("GAME BOY ADVANCE", "AGB", (28, 32, 85), (48, 60, 150), (78, 96, 196, 255)),
    "gb": ("GAME BOY", "DMG", (38, 48, 32), (68, 86, 52), (96, 122, 68, 255)),
    "gbc": ("GAME BOY COLOR", "CGB", (45, 24, 72), (82, 42, 135), (128, 68, 196, 255)),
    "nes": ("NINTENDO (NES)", "NES", (62, 22, 26), (115, 34, 42), (188, 46, 56, 255)),
    "snes": ("SUPER NINTENDO", "SNS", (42, 36, 78), (76, 65, 142), (112, 92, 196, 255)),
    "md": ("SEGA GENESIS", "GEN", (18, 28, 56), (32, 54, 118), (44, 92, 196, 255)),
    "nds": ("NINTENDO DS", "NTR", (28, 34, 46), (54, 66, 88), (72, 118, 204, 255)),
}

# 16x16 multi-color pixel-art emblems for each game genre & console type
PIXEL_EMBLEMS: Dict[str, Tuple[Dict[str, RGBA], Tuple[str, ...]]] = {
    "rpg": (
        {
            "1": (20, 14, 38, 255),
            "2": (64, 116, 232, 255),
            "3": (255, 212, 64, 255),
            "4": (235, 245, 255, 255),
            "5": (255, 58, 92, 255),
        },
        (
            ".......11.......",
            "......1441......",
            ".....134421.....",
            "....13244231....",
            "..113224422311..",
            ".13322344322331.",
            ".13223555532231.",
            ".13223545532231.",
            "..132235532231..",
            "..133223322331..",
            "...1332442331...",
            "...1133443311...",
            "..133133331331..",
            "....11.11.11....",
            "......1331......",
            ".......11.......",
        ),
    ),
    "slg": (
        {
            "1": (20, 22, 32, 255),
            "2": (198, 36, 52, 255),
            "3": (248, 198, 60, 255),
            "4": (222, 234, 248, 255),
            "5": (255, 246, 196, 255),
        },
        (
            "..131..11..131..",
            ".13531133113531.",
            ".13333355333331.",
            "..111111111111..",
            ".13222233222231.",
            ".13222355322231.",
            ".13223555532231.",
            ".13233355333231.",
            "..132235532231..",
            "..132353353231..",
            "...1322222231...",
            "....13222231....",
            "...1413223141...",
            "..141.1331.141..",
            ".131...11...131.",
            "................",
        ),
    ),
    "act": (
        {
            "1": (24, 18, 10, 255),
            "2": (255, 152, 24, 255),
            "3": (255, 232, 52, 255),
            "4": (255, 255, 228, 255),
            "5": (56, 228, 255, 255),
        },
        (
            ".......11.......",
            "......1441......",
            "......1331......",
            ".....143321.....",
            ".11111333211111.",
            "144333333222221.",
            ".1433133312221..",
            "..13313331221...",
            "...133333221....",
            "..13333322221...",
            "..13331112221...",
            ".13321...12221..",
            ".1321.....1221..",
            ".111.......111..",
            "................",
            "................",
        ),
    ),
    "arpg": (
        {
            "1": (18, 8, 24, 255),
            "2": (162, 18, 46, 255),
            "3": (248, 52, 84, 255),
            "4": (228, 236, 255, 255),
            "5": (255, 198, 68, 255),
        },
        (
            "....111111......",
            "..1133332211....",
            ".133331111221...",
            ".13331...1411...",
            "13331...14541...",
            "13331..1454541..",
            "13331...14541...",
            "13331....141....",
            "133331...141....",
            ".133331114441...",
            ".12333332141....",
            "..1122222141....",
            "....11111141....",
            ".........151....",
            ".........111....",
            "................",
        ),
    ),
    "avg": (
        {
            "1": (14, 28, 18, 255),
            "2": (210, 152, 24, 255),
            "3": (255, 224, 58, 255),
            "4": (255, 252, 218, 255),
            "5": (58, 226, 126, 255),
        },
        (
            ".......11.......",
            "......1441......",
            ".....143321.....",
            ".....133321.....",
            "....14333221....",
            "....12222221....",
            "...1111111111...",
            "..1441....1441..",
            ".143321..143321.",
            ".133321..133321.",
            "1433322114333221",
            "1222222112222221",
            "1111111111111111",
            "..55..5555..55..",
            "....55....55....",
            "................",
        ),
    ),
    "ftg": (
        {
            "1": (26, 10, 10, 255),
            "2": (218, 32, 36, 255),
            "3": (255, 118, 24, 255),
            "4": (255, 224, 48, 255),
            "5": (255, 255, 238, 255),
        },
        (
            ".......11.......",
            ".....11221..11..",
            "....12233211221.",
            "...123344322321.",
            "..1234455433321.",
            ".12345555544321.",
            ".12345555554321.",
            "1233455555543321",
            "1234455555443321",
            "1223445554433221",
            ".12233444433221.",
            "..122233332221..",
            "...1122222211...",
            ".....111111.....",
            "................",
            "................",
        ),
    ),
    "rac": (
        {
            "1": (18, 22, 32, 255),
            "2": (245, 248, 255, 255),
            "3": (255, 208, 46, 255),
            "4": (255, 52, 64, 255),
            "5": (56, 208, 255, 255),
        },
        (
            ".131111111111...",
            ".1312211221121..",
            ".1312211221121..",
            ".1311122112211..",
            ".1311122112211..",
            ".1312211221121..",
            ".1312211221121..",
            ".1311122112211..",
            ".131111111111...",
            ".131..44444444..",
            ".131.4455555544.",
            ".131..44444444..",
            ".131............",
            ".131............",
            ".111............",
            "................",
        ),
    ),
    "puz": (
        {
            "1": (22, 16, 38, 255),
            "2": (255, 68, 152, 255),
            "3": (48, 232, 196, 255),
            "4": (255, 226, 52, 255),
            "5": (255, 255, 255, 255),
        },
        (
            ".11111111.......",
            ".15221521.......",
            ".1222122111111..",
            ".1222122154441..",
            ".1111111144441..",
            ".15221..144441..",
            ".12221..111111..",
            ".12221..15331...",
            ".11111..13331...",
            "....111113331...",
            "....153311111...",
            "....133315331...",
            "....133313331...",
            "....111111111...",
            "................",
            "................",
        ),
    ),
    "classic": (
        {
            "1": (18, 20, 38, 255),
            "2": (86, 72, 184, 255),
            "3": (136, 120, 230, 255),
            "4": (105, 224, 255, 255),
            "5": (215, 222, 238, 255),
        },
        (
            "................",
            "................",
            "..111111111111..",
            ".13333333333331.",
            "1322111111112231",
            "1222144444412221",
            "1252144444412521",
            "1555144444415251",
            "1252144444412221",
            "1222111111112221",
            ".12222222222221.",
            "..111111111111..",
            "................",
            "................",
            "................",
            "................",
        ),
    ),
}


class TemplateTheme:
    """Visual styling specification for a game genre or console template."""

    def __init__(
        self,
        key: str,
        badge_short: str,
        name_zh: str,
        bg_top: RGB,
        bg_bot: RGB,
        header_bg: RGBA,
        accent_primary: RGBA,
        accent_secondary: RGBA,
        border_outer: RGBA,
        border_inner: RGBA,
        bezel_main: RGB,
        bezel_dark: RGB,
        bezel_led: RGBA,
        pattern: str,
        emblem: str,
    ) -> None:
        self.key = key
        self.badge_short = badge_short
        self.name_zh = name_zh
        self.bg_top = bg_top
        self.bg_bot = bg_bot
        self.header_bg = header_bg
        self.accent_primary = accent_primary
        self.accent_secondary = accent_secondary
        self.border_outer = border_outer
        self.border_inner = border_inner
        self.bezel_main = bezel_main
        self.bezel_dark = bezel_dark
        self.bezel_led = bezel_led
        self.pattern = pattern
        self.emblem = emblem


GENRE_TEMPLATES: Dict[str, TemplateTheme] = {
    "rpg": TemplateTheme(
        key="rpg",
        badge_short="RPG",
        name_zh="奇幻角色扮演 (RPG)",
        bg_top=(46, 30, 98),
        bg_bot=(18, 12, 44),
        header_bg=(16, 12, 36, 255),
        accent_primary=(255, 212, 72, 255),
        accent_secondary=(86, 56, 178, 255),
        border_outer=(238, 192, 58, 255),
        border_inner=(118, 92, 220, 255),
        bezel_main=(92, 68, 188),
        bezel_dark=(42, 28, 96),
        bezel_led=(80, 235, 255, 255),
        pattern="stars",
        emblem="rpg",
    ),
    "slg": TemplateTheme(
        key="slg",
        badge_short="SLG",
        name_zh="策略战棋 (SLG)",
        bg_top=(68, 26, 38),
        bg_bot=(20, 24, 38),
        header_bg=(18, 20, 30, 255),
        accent_primary=(245, 192, 62, 255),
        accent_secondary=(182, 38, 52, 255),
        border_outer=(215, 168, 54, 255),
        border_inner=(165, 42, 56, 255),
        bezel_main=(156, 42, 56),
        bezel_dark=(52, 22, 30),
        bezel_led=(255, 196, 48, 255),
        pattern="grid",
        emblem="slg",
    ),
    "act": TemplateTheme(
        key="act",
        badge_short="ACT",
        name_zh="横版动作闯关 (ACT)",
        bg_top=(24, 82, 162),
        bg_bot=(12, 28, 62),
        header_bg=(12, 22, 46, 255),
        accent_primary=(255, 224, 52, 255),
        accent_secondary=(28, 128, 228, 255),
        border_outer=(64, 212, 255, 255),
        border_inner=(255, 216, 48, 255),
        bezel_main=(42, 118, 215),
        bezel_dark=(18, 48, 102),
        bezel_led=(72, 255, 128, 255),
        pattern="speedlines",
        emblem="act",
    ),
    "arpg": TemplateTheme(
        key="arpg",
        badge_short="ARPG",
        name_zh="银河城/动作RPG (ARPG)",
        bg_top=(74, 18, 44),
        bg_bot=(16, 10, 24),
        header_bg=(14, 8, 20, 255),
        accent_primary=(255, 64, 96, 255),
        accent_secondary=(128, 24, 62, 255),
        border_outer=(220, 48, 82, 255),
        border_inner=(168, 178, 205, 255),
        bezel_main=(56, 24, 48),
        bezel_dark=(24, 10, 22),
        bezel_led=(255, 48, 82, 255),
        pattern="gothic",
        emblem="arpg",
    ),
    "avg": TemplateTheme(
        key="avg",
        badge_short="AVG",
        name_zh="冒险探索/解谜 (AVG)",
        bg_top=(24, 82, 54),
        bg_bot=(10, 32, 22),
        header_bg=(10, 26, 18, 255),
        accent_primary=(255, 218, 68, 255),
        accent_secondary=(36, 128, 78, 255),
        border_outer=(238, 196, 56, 255),
        border_inner=(62, 188, 112, 255),
        bezel_main=(38, 126, 78),
        bezel_dark=(16, 56, 34),
        bezel_led=(120, 255, 96, 255),
        pattern="relic",
        emblem="avg",
    ),
    "ftg": TemplateTheme(
        key="ftg",
        badge_short="FTG",
        name_zh="街机格斗对战 (FTG)",
        bg_top=(96, 22, 26),
        bg_bot=(24, 10, 14),
        header_bg=(20, 8, 10, 255),
        accent_primary=(255, 208, 42, 255),
        accent_secondary=(208, 38, 38, 255),
        border_outer=(255, 126, 36, 255),
        border_inner=(255, 216, 52, 255),
        bezel_main=(188, 38, 42),
        bezel_dark=(68, 14, 18),
        bezel_led=(255, 160, 32, 255),
        pattern="vs_slashes",
        emblem="ftg",
    ),
    "rac": TemplateTheme(
        key="rac",
        badge_short="RAC",
        name_zh="极速赛车竞速 (RAC)",
        bg_top=(32, 58, 98),
        bg_bot=(14, 20, 34),
        header_bg=(14, 18, 28, 255),
        accent_primary=(255, 226, 48, 255),
        accent_secondary=(220, 42, 56, 255),
        border_outer=(230, 238, 252, 255),
        border_inner=(255, 58, 72, 255),
        bezel_main=(52, 88, 158),
        bezel_dark=(22, 36, 68),
        bezel_led=(64, 228, 255, 255),
        pattern="checkered",
        emblem="rac",
    ),
    "puz": TemplateTheme(
        key="puz",
        badge_short="PUZ",
        name_zh="益智休闲/体育 (PUZ)",
        bg_top=(68, 36, 118),
        bg_bot=(24, 16, 52),
        header_bg=(20, 12, 42, 255),
        accent_primary=(255, 226, 58, 255),
        accent_secondary=(236, 64, 148, 255),
        border_outer=(58, 232, 198, 255),
        border_inner=(255, 96, 172, 255),
        bezel_main=(228, 112, 42),
        bezel_dark=(98, 42, 16),
        bezel_led=(110, 255, 90, 255),
        pattern="dots",
        emblem="puz",
    ),
    "classic": TemplateTheme(
        key="classic",
        badge_short="GBA",
        name_zh="经典主机原色 (CLASSIC)",
        bg_top=(48, 60, 150),
        bg_bot=(24, 28, 72),
        header_bg=(18, 20, 38, 255),
        accent_primary=(255, 215, 90, 255),
        accent_secondary=(78, 96, 196, 255),
        border_outer=(96, 120, 215, 255),
        border_inner=(142, 164, 245, 255),
        bezel_main=(116, 94, 192),
        bezel_dark=(52, 38, 102),
        bezel_led=(90, 245, 110, 255),
        pattern="scanlines",
        emblem="classic",
    ),
}

# Console-specific overrides when packaging non-GBA systems with default style
CONSOLE_TEMPLATE_OVERRIDES: Dict[str, TemplateTheme] = {
    "gb": TemplateTheme(
        key="gb",
        badge_short="DMG",
        name_zh="Game Boy 经典点阵绿",
        bg_top=(68, 86, 52),
        bg_bot=(32, 42, 26),
        header_bg=(24, 30, 20, 255),
        accent_primary=(186, 218, 85, 255),
        accent_secondary=(138, 36, 78, 255),
        border_outer=(148, 168, 128, 255),
        border_inner=(186, 218, 85, 255),
        bezel_main=(138, 134, 128),
        bezel_dark=(72, 70, 66),
        bezel_led=(255, 48, 48, 255),
        pattern="scanlines",
        emblem="classic",
    ),
    "gbc": TemplateTheme(
        key="gbc",
        badge_short="CGB",
        name_zh="Game Boy Color 原子紫",
        bg_top=(82, 42, 135),
        bg_bot=(36, 18, 62),
        header_bg=(24, 12, 42, 255),
        accent_primary=(255, 216, 64, 255),
        accent_secondary=(136, 68, 208, 255),
        border_outer=(162, 98, 232, 255),
        border_inner=(64, 224, 208, 255),
        bezel_main=(108, 58, 168),
        bezel_dark=(48, 24, 82),
        bezel_led=(255, 56, 56, 255),
        pattern="dots",
        emblem="classic",
    ),
    "nes": TemplateTheme(
        key="nes",
        badge_short="NES",
        name_zh="FC / NES 红白机经典",
        bg_top=(115, 34, 42),
        bg_bot=(42, 16, 20),
        header_bg=(28, 12, 14, 255),
        accent_primary=(255, 215, 78, 255),
        accent_secondary=(196, 42, 52, 255),
        border_outer=(215, 182, 112, 255),
        border_inner=(220, 52, 62, 255),
        bezel_main=(168, 38, 46),
        bezel_dark=(68, 18, 22),
        bezel_led=(255, 64, 48, 255),
        pattern="scanlines",
        emblem="act",
    ),
    "snes": TemplateTheme(
        key="snes",
        badge_short="SNS",
        name_zh="SFC / SNES 超任经典",
        bg_top=(76, 65, 142),
        bg_bot=(32, 28, 64),
        header_bg=(22, 18, 44, 255),
        accent_primary=(255, 218, 68, 255),
        accent_secondary=(112, 92, 196, 255),
        border_outer=(168, 162, 196, 255),
        border_inner=(126, 106, 218, 255),
        bezel_main=(118, 108, 168),
        bezel_dark=(56, 48, 88),
        bezel_led=(255, 58, 58, 255),
        pattern="stars",
        emblem="rpg",
    ),
    "md": TemplateTheme(
        key="md",
        badge_short="GEN",
        name_zh="Sega Genesis / MD 黑金",
        bg_top=(32, 54, 118),
        bg_bot=(14, 20, 42),
        header_bg=(12, 16, 32, 255),
        accent_primary=(248, 204, 68, 255),
        accent_secondary=(44, 92, 196, 255),
        border_outer=(220, 178, 56, 255),
        border_inner=(68, 128, 235, 255),
        bezel_main=(38, 42, 54),
        bezel_dark=(16, 18, 24),
        bezel_led=(255, 52, 52, 255),
        pattern="grid",
        emblem="ftg",
    ),
    "nds": TemplateTheme(
        key="nds",
        badge_short="NTR",
        name_zh="Nintendo DS 双屏钛银",
        bg_top=(52, 68, 96),
        bg_bot=(22, 28, 42),
        header_bg=(16, 20, 30, 255),
        accent_primary=(96, 212, 255, 255),
        accent_secondary=(64, 116, 204, 255),
        border_outer=(176, 192, 215, 255),
        border_inner=(82, 168, 245, 255),
        bezel_main=(72, 84, 106),
        bezel_dark=(32, 38, 52),
        bezel_led=(72, 245, 118, 255),
        pattern="grid",
        emblem="classic",
    ),
}


def resolve_template_theme(
    meta: GameMetadata,
    template: Optional[str] = None,
) -> TemplateTheme:
    """
    Resolve the `TemplateTheme` for `meta` and optional explicit `template` key.
    Priority:
      1. Explicit `template` parameter (if in `GENRE_TEMPLATES` or `CONSOLE_TEMPLATE_OVERRIDES`)
      2. Platform console template (`CONSOLE_TEMPLATE_OVERRIDES`) for non-GBA systems (`gb`, `gbc`, `nes`, `snes`, `md`, `nds`)
      3. Detected `meta.genre` (or `'classic'` GBA console theme)
    """
    if template and template.lower() not in ("auto", ""):
        t_key = template.lower()
        if t_key in GENRE_TEMPLATES:
            return GENRE_TEMPLATES[t_key]
        if t_key in CONSOLE_TEMPLATE_OVERRIDES:
            return CONSOLE_TEMPLATE_OVERRIDES[t_key]

    sys_key = (getattr(meta, "system", "gba") or "gba").lower()
    if sys_key != "gba" and sys_key in CONSOLE_TEMPLATE_OVERRIDES:
        return CONSOLE_TEMPLATE_OVERRIDES[sys_key]

    genre_key = (getattr(meta, "genre", "") or "").lower()
    if not genre_key or genre_key == "auto":
        genre_key = detect_game_genre(
            game_code=getattr(meta, "game_code", ""),
            title=getattr(meta, "short_title", ""),
            canonical_name=getattr(meta, "canonical_name", ""),
        )

    return GENRE_TEMPLATES.get(genre_key, GENRE_TEMPLATES["classic"])


def draw_pixel_emblem(
    img: RGBAImage,
    emblem_key: str,
    x0: int,
    y0: int,
    scale: int = 1,
    shadow: bool = True,
) -> None:
    """Render a 16x16 multi-color pixel-art emblem at `(x0, y0)` with given `scale`."""
    palette, rows = PIXEL_EMBLEMS.get(emblem_key, PIXEL_EMBLEMS["classic"])
    if shadow:
        for ry, row in enumerate(rows):
            for rx, ch in enumerate(row):
                if ch in palette:
                    fill_rect(
                        img,
                        x0 + rx * scale + scale,
                        y0 + ry * scale + scale,
                        scale,
                        scale,
                        (0, 0, 0, 140),
                    )
    for ry, row in enumerate(rows):
        for rx, ch in enumerate(row):
            col = palette.get(ch)
            if col:
                fill_rect(img, x0 + rx * scale, y0 + ry * scale, scale, scale, col)


def draw_bg_pattern(
    img: RGBAImage,
    x0: int,
    y0: int,
    w: int,
    h: int,
    pattern: str,
    accent: RGBA,
) -> None:
    """Render subtle, atmospheric genre-specific background patterns."""
    ar, ag, ab, _ = accent
    if pattern == "grid":
        # Tactical war-room grid + radar crosshair
        grid_col = (ar, ag, ab, 24)
        for gx in range(x0 + 8, x0 + w, 16):
            fill_rect(img, gx, y0, 1, h, grid_col)
        for gy in range(y0 + 8, y0 + h, 16):
            fill_rect(img, x0, gy, w, 1, grid_col)
        for gx in range(x0 + 8, x0 + w, 32):
            for gy in range(y0 + 8, y0 + h, 32):
                fill_rect(img, gx - 2, gy, 5, 1, (ar, ag, ab, 55))
                fill_rect(img, gx, gy - 2, 1, 5, (ar, ag, ab, 55))
    elif pattern == "stars":
        # Twinkling RPG starlight & diamond sparkles
        for i in range(28):
            sx = x0 + ((i * 73 + 19) % max(1, w - 6)) + 3
            sy = y0 + ((i * 41 + 11) % max(1, h - 6)) + 3
            if i % 4 == 0:
                fill_rect(img, sx - 2, sy, 5, 1, (ar, ag, ab, 75))
                fill_rect(img, sx, sy - 2, 1, 5, (ar, ag, ab, 75))
                fill_rect(img, sx, sy, 1, 1, (255, 255, 255, 160))
            else:
                fill_rect(img, sx, sy, 2 if (i & 1) else 1, 1, (220, 235, 255, 65))
    elif pattern == "speedlines":
        # Dynamic arcade diagonal speed streaks
        for i in range(-h, w, 18):
            for step in range(0, h, 2):
                px = x0 + i + step
                py = y0 + h - 1 - step
                if x0 <= px < x0 + w - 4 and y0 <= py < y0 + h:
                    fill_rect(img, px, py, 4, 1, (ar, ag, ab, 30 if (i // 18) % 2 == 0 else 16))
    elif pattern == "gothic":
        # Gothic diamond lattice & crimson moon glow
        for gy in range(y0, y0 + h, 16):
            for gx in range(x0, x0 + w, 16):
                fill_rect(img, gx + 7, gy, 2, 2, (ar, ag, ab, 32))
                fill_rect(img, gx, gy + 7, 2, 2, (ar, ag, ab, 24))
                fill_rect(img, gx + 4, gy + 4, 1, 1, (ar, ag, ab, 40))
                fill_rect(img, gx + 11, gy + 11, 1, 1, (ar, ag, ab, 40))
    elif pattern == "relic":
        # Ancient Triforce / diamond tapestry
        for gy in range(y0 + 6, y0 + h - 6, 20):
            for gx in range(x0 + 8, x0 + w - 8, 24):
                fill_rect(img, gx + 3, gy, 2, 2, (ar, ag, ab, 42))
                fill_rect(img, gx + 1, gy + 2, 2, 2, (ar, ag, ab, 34))
                fill_rect(img, gx + 5, gy + 2, 2, 2, (ar, ag, ab, 34))
    elif pattern == "vs_slashes":
        # Arcade fighting VS diagonal hazard bars & sparks
        for i in range(-h, w, 22):
            for step in range(0, h, 2):
                px = x0 + i + step // 2
                py = y0 + step
                if x0 <= px < x0 + w - 6 and y0 <= py < y0 + h:
                    fill_rect(img, px, py, 6, 1, (255, 110, 30, 26))
        for i in range(16):
            sx = x0 + ((i * 61 + 7) % max(1, w - 4))
            sy = y0 + ((i * 37 + 13) % max(1, h - 4))
            fill_rect(img, sx, sy, 2, 2, (255, 210, 60, 60))
    elif pattern == "checkered":
        # Carbon fiber weave & racing track ticks
        for gy in range(y0, y0 + h, 8):
            for gx in range(x0, x0 + w, 8):
                if ((gx // 8) + (gy // 8)) % 2 == 0:
                    fill_rect(img, gx, gy, 4, 4, (255, 255, 255, 14))
    elif pattern == "dots":
        # Playful pop-art halftone dots & mini blocks
        palette_dots = [
            (255, 82, 168, 38),
            (64, 240, 208, 38),
            (255, 226, 64, 38),
        ]
        for idx_y, gy in enumerate(range(y0 + 6, y0 + h - 4, 14)):
            for idx_x, gx in enumerate(range(x0 + 6, x0 + w - 4, 14)):
                ox = gx + (7 if (idx_y & 1) else 0)
                if ox + 3 < x0 + w:
                    col = palette_dots[(idx_x + idx_y) % 3]
                    fill_rect(img, ox, gy, 3, 3, col)
    else:
        # Classic subtle scanlines
        for gy in range(y0, y0 + h, 3):
            fill_rect(img, x0, gy, w, 1, (255, 255, 255, 12))


def draw_theme_header_trim(
    img: RGBAImage,
    x0: int,
    y0: int,
    w: int,
    theme: TemplateTheme,
) -> None:
    """Draw genre-specific decorative trim along a horizontal bar at `(x0, y0, w)`."""
    if theme.key == "rac":
        # 2-row black & white checkered racing strip
        for cx in range(x0, x0 + w, 4):
            idx = (cx - x0) // 4
            c1 = (245, 248, 255, 255) if (idx % 2 == 0) else (20, 24, 34, 255)
            c2 = (20, 24, 34, 255) if (idx % 2 == 0) else (245, 248, 255, 255)
            fill_rect(img, cx, y0, min(4, x0 + w - cx), 2, c1)
            fill_rect(img, cx, y0 + 2, min(4, x0 + w - cx), 2, c2)
    elif theme.key == "ftg":
        # Arcade VS health-bar gradient strip (Red -> Orange -> Yellow -> KO center)
        fill_rect(img, x0, y0, w, 3, (40, 12, 14, 255))
        half = w // 2
        for dx in range(half - 6):
            t = dx / max(1, half - 7)
            r = 255
            g = int(48 + 180 * t)
            b = int(32 * (1.0 - t))
            fill_rect(img, x0 + dx, y0, 1, 3, (r, g, b, 255))
            fill_rect(img, x0 + w - 1 - dx, y0, 1, 3, (r, g, b, 255))
        fill_rect(img, x0 + half - 5, y0 - 1, 10, 5, (255, 215, 48, 255))
    elif theme.key in ("rpg", "avg"):
        # Ornate double gold filigree line with center diamond
        fill_rect(img, x0, y0, w, 2, theme.accent_primary)
        fill_rect(img, x0, y0 + 2, w, 1, (20, 16, 36, 220))
        fill_rect(img, x0, y0 + 3, w, 1, theme.border_inner)
        mid = x0 + w // 2
        fill_rect(img, mid - 3, y0 - 1, 6, 4, theme.accent_primary)
        fill_rect(img, mid - 1, y0, 2, 2, (255, 255, 255, 255))
    elif theme.key == "slg":
        # Tactical brass & crimson command bar with chevron ticks
        fill_rect(img, x0, y0, w, 3, theme.accent_secondary)
        fill_rect(img, x0, y0 + 3, w, 1, theme.accent_primary)
        for cx in range(x0 + 6, x0 + w - 6, 12):
            fill_rect(img, cx, y0, 4, 3, theme.accent_primary)
    elif theme.key == "puz":
        # Colorful pop-arcade block segments
        seg_cols = [
            (255, 72, 156, 255),
            (255, 226, 56, 255),
            (56, 232, 196, 255),
            (92, 168, 255, 255),
        ]
        for idx, cx in enumerate(range(x0, x0 + w, 16)):
            fill_rect(img, cx, y0, min(16, x0 + w - cx), 3, seg_cols[idx % 4])
    else:
        fill_rect(img, x0, y0, w, 2, theme.accent_primary)
        fill_rect(img, x0, y0 + 2, w, 1, theme.accent_secondary)


def draw_corner_ornaments(
    img: RGBAImage,
    x0: int,
    y0: int,
    w: int,
    h: int,
    theme: TemplateTheme,
    size: int = 4,
) -> None:
    """Draw genre-themed corner studs/gems/brackets at the 4 corners of `(x0, y0, w, h)`."""
    corners = [
        (x0 + 2, y0 + 2),
        (x0 + w - 2 - size, y0 + 2),
        (x0 + 2, y0 + h - 2 - size),
        (x0 + w - 2 - size, y0 + h - 2 - size),
    ]
    if theme.key == "puz":
        btn_cols = [
            (255, 72, 152, 255),
            (255, 226, 56, 255),
            (56, 232, 196, 255),
            (88, 164, 255, 255),
        ]
        for idx, (cx, cy) in enumerate(corners):
            fill_rect(img, cx, cy, size, size, btn_cols[idx])
            fill_rect(img, cx, cy, 1, 1, (255, 255, 255, 220))
    else:
        for cx, cy in corners:
            fill_rect(img, cx, cy, size, size, theme.accent_primary)
            if size >= 3:
                fill_rect(img, cx + 1, cy + 1, size - 2, size - 2, (255, 255, 255, 210))


def generate_stylized_cover(
    meta: GameMetadata,
    width: int = 240,
    height: int = 160,
    template: Optional[str] = None,
) -> RGBAImage:
    """
    Generate a rich, genre- and console-themed cartridge/showcase cover image
    when offline, when no boxart is found, or when previewing templates.
    """
    sys_key = (getattr(meta, "system", "gba") or "gba").lower()
    label, sys_prefix, _, _, _ = CONSOLE_THEMES.get(sys_key, CONSOLE_THEMES["gba"])
    theme = resolve_template_theme(meta, template=template)

    img = RGBAImage(width, height)
    c_top = theme.bg_top
    c_bot = theme.bg_bot
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(c_top[0] + (c_bot[0] - c_top[0]) * t)
        g = int(c_top[1] + (c_bot[1] - c_top[1]) * t)
        b = int(c_top[2] + (c_bot[2] - c_top[2]) * t)
        row = y * width
        for x in range(width):
            img.pixels[row + x] = (r, g, b, 255)

    # Atmospheric genre background pattern
    draw_bg_pattern(img, 0, 28, width, height - 56, theme.pattern, theme.accent_primary)

    # Top header bar with Console label + LED + Genre badge pill
    fill_rect(img, 0, 0, width, 26, theme.header_bg)
    draw_theme_header_trim(img, 0, 26, width, theme)

    # Glowing console power LED dot on header left
    fill_rect(img, 8, 10, 4, 4, theme.bezel_led)
    fill_rect(img, 9, 11, 2, 2, (255, 255, 255, 220))
    draw_text(img, label, 16, 9, (235, 242, 255, 255), scale=1, max_width=width - 72)

    # Top-right Genre badge pill
    badge_text = theme.badge_short
    badge_w = len(badge_text) * 6 + 10
    badge_x = width - badge_w - 8
    fill_rect(img, badge_x, 6, badge_w, 13, theme.accent_secondary)
    draw_border(img, badge_x, 6, badge_w, 13, theme.accent_primary, thickness=1)
    draw_text(
        img,
        badge_text,
        badge_x + 5,
        9,
        (255, 255, 255, 255),
        scale=1,
        shadow_color=None,
    )

    # Right-side ornate emblem medallion (40x40 box with 32x32 2x pixel emblem)
    if width >= 180 and height >= 120:
        med_size = 42
        med_x = width - med_size - 12
        med_y = 42
        fill_rect(img, med_x + 2, med_y + 2, med_size, med_size, (0, 0, 0, 120))
        fill_rect(img, med_x, med_y, med_size, med_size, (16, 18, 32, 210))
        draw_border(img, med_x, med_y, med_size, med_size, theme.border_outer, thickness=2)
        draw_border(img, med_x + 2, med_y + 2, med_size - 4, med_size - 4, theme.border_inner, thickness=1)
        draw_pixel_emblem(img, theme.emblem, med_x + 5, med_y + 5, scale=2, shadow=True)
        title_max_w = med_x - 20
        max_chars = max(10, title_max_w // 12)
    else:
        # Compact icon cover (e.g. 128x128)
        draw_pixel_emblem(img, theme.emblem, width - 38, height - 66, scale=2, shadow=True)
        title_max_w = width - 24
        max_chars = max(8, title_max_w // 12)

    # Wrap and render title
    words = meta.short_title.split()
    lines: List[str] = []
    cur = ""
    for w_word in words:
        test = f"{cur} {w_word}".strip()
        if len(test) <= max_chars:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w_word[:max_chars]
    if cur:
        lines.append(cur)
    if not lines:
        lines = [meta.game_code]

    y_pos = 42
    for idx, line in enumerate(lines[:3]):
        col = (255, 255, 255, 255) if idx == 0 else (240, 244, 255, 255)
        draw_text(img, line, 14, y_pos, col, scale=2, max_width=title_max_w)
        y_pos += 19

    # Decorative accent bar below title
    rule_y = min(height - 38, max(y_pos + 3, 98))
    fill_rect(img, 14, rule_y, min(110, title_max_w), 2, theme.accent_primary)
    fill_rect(img, 14, rule_y + 2, min(68, title_max_w), 1, theme.accent_secondary)

    # Bottom cartridge metadata strip
    fill_rect(img, 0, height - 28, width, 28, theme.header_bg)
    fill_rect(img, 0, height - 28, width, 2, theme.border_outer)

    # Cartridge grip grooves on left of footer
    for gy in range(height - 22, height - 6, 4):
        fill_rect(img, 8, gy, 6, 2, theme.accent_secondary)

    draw_text(
        img,
        f"{meta.publisher[:16]} ({meta.release_year})",
        18,
        height - 17,
        (205, 218, 245, 255),
        scale=1,
        max_width=width - 96,
    )

    # Serial number gold/accent pill on bottom right
    serial_str = f"{sys_prefix}-{meta.game_code[:4].upper()}"
    ser_w = len(serial_str) * 6 + 8
    ser_x = width - ser_w - 8
    fill_rect(img, ser_x, height - 21, ser_w, 13, (28, 30, 48, 255))
    draw_border(img, ser_x, height - 21, ser_w, 13, theme.accent_primary, thickness=1)
    draw_text(
        img,
        serial_str,
        ser_x + 4,
        height - 18,
        theme.accent_primary,
        scale=1,
        shadow_color=None,
    )

    # Outer double-beveled ornate border + corner studs
    draw_border(img, 0, 0, width, height, theme.border_outer, thickness=2)
    draw_border(img, 2, 2, width - 4, height - 4, theme.border_inner, thickness=1)
    draw_corner_ornaments(img, 2, 29, width - 4, height - 57, theme, size=4)
    return img


def compose_vc_icons(
    meta: GameMetadata,
    source_image: Optional[RGBAImage] = None,
    template: Optional[str] = None,
) -> Tuple[RGBAImage, RGBAImage]:
    """
    Create the 24x24 and 48x48 3DS SMDH icons with a genre/console-themed Virtual Console bezel frame.
    Returns (icon_24x24, icon_48x48).
    """
    theme = resolve_template_theme(meta, template=template)
    if source_image is None:
        if meta.boxart_path and os.path.exists(meta.boxart_path):
            source_image = load_image_file(meta.boxart_path)
        elif meta.title_screen_path and os.path.exists(meta.title_screen_path):
            source_image = load_image_file(meta.title_screen_path)
        else:
            source_image = generate_stylized_cover(meta, 128, 128, template=template)

    # Build 48x48 icon: themed outer bezel + inner highlight + 40x40 art + corner studs
    icon48 = RGBAImage(48, 48, [(*theme.bezel_dark, 255)] * (48 * 48))
    draw_border(icon48, 0, 0, 48, 48, theme.border_outer, thickness=1)
    draw_border(icon48, 1, 1, 46, 46, (*theme.bezel_main, 255), thickness=2)
    inner48 = resize_rgba(source_image, 40, 40)
    blit_image(icon48, inner48, 4, 4)
    draw_border(icon48, 3, 3, 42, 42, (14, 16, 24, 255), thickness=1)
    draw_corner_ornaments(icon48, 0, 0, 48, 48, theme, size=2)

    # Build 24x24 icon: outer themed 1px border + 22x22 inner art
    icon24 = RGBAImage(24, 24, [(*theme.bezel_dark, 255)] * (24 * 24))
    inner24 = resize_rgba(source_image, 22, 22)
    blit_image(icon24, inner24, 1, 1)
    draw_border(icon24, 0, 0, 24, 24, theme.border_outer, thickness=1)

    return icon24, icon48


def compose_vc_banner_256x128(
    meta: GameMetadata,
    boxart_img: Optional[RGBAImage] = None,
    title_screen_img: Optional[RGBAImage] = None,
    banner_style: str = "vc",
    template: Optional[str] = None,
) -> RGBAImage:
    """
    Create the 256x128 top-screen 3DS Banner image tailored to the game's genre and console template.
    - `banner_style == "vc"` (default): Nintendo 3DS Virtual Console showcase card with
      framed Boxart/Screen preview on the left, genre/console metadata plate & pixel emblem on the right.
    - `banner_style == "full"`: Stretches/fits the cover image across the full 256x128 canvas.
    """
    if boxart_img is None and meta.boxart_path and os.path.exists(meta.boxart_path):
        try:
            boxart_img = load_image_file(meta.boxart_path)
        except Exception:
            boxart_img = None

    if title_screen_img is None and meta.title_screen_path and os.path.exists(meta.title_screen_path):
        try:
            title_screen_img = load_image_file(meta.title_screen_path)
        except Exception:
            title_screen_img = None

    theme = resolve_template_theme(meta, template=template)
    primary_img = (
        boxart_img
        or title_screen_img
        or generate_stylized_cover(meta, 240, 160, template=template)
    )

    if banner_style == "full":
        return resize_rgba(primary_img, 256, 128)

    # Create 256x128 canvas with genre/console themed backdrop gradient
    canvas = RGBAImage(256, 128)
    c_top = theme.bg_top
    c_bot = theme.bg_bot
    for y in range(128):
        t = y / 127.0
        r = int(c_top[0] + (c_bot[0] - c_top[0]) * t)
        g = int(c_top[1] + (c_bot[1] - c_top[1]) * t)
        b = int(c_top[2] + (c_bot[2] - c_top[2]) * t)
        row = y * 256
        for x in range(256):
            canvas.pixels[row + x] = (r, g, b, 255)

    # Render atmospheric genre pattern across canvas
    draw_bg_pattern(canvas, 2, 6, 252, 116, theme.pattern, theme.accent_primary)

    # Top & bottom themed accent bars
    draw_theme_header_trim(canvas, 0, 1, 256, theme)
    fill_rect(canvas, 0, 123, 256, 2, theme.accent_secondary)
    fill_rect(canvas, 0, 125, 256, 3, theme.border_outer)

    # Left side: Boxart &/or Screen preview inside a platform-specific console hardware frame
    sys_key = (getattr(meta, "system", "gba") or "gba").lower()
    if boxart_img is not None:
        # Fit boxart cleanly into a 94x94 box inside a 100x100 ornate frame at (10, 14)
        fill_rect(canvas, 14, 18, 100, 100, (0, 0, 0, 150))  # drop shadow
        fill_rect(canvas, 10, 14, 100, 100, (20, 22, 36, 255))
        scaled_box = resize_rgba(boxart_img, 92, 92)
        blit_image(canvas, scaled_box, 14, 18)
        draw_border(canvas, 10, 14, 100, 100, theme.border_outer, thickness=2)
        draw_border(canvas, 12, 16, 96, 96, theme.border_inner, thickness=1)
        draw_corner_ornaments(canvas, 10, 14, 100, 100, theme, size=3)
        right_x = 118
    else:
        fill_rect(canvas, 10, 20, 116, 94, (0, 0, 0, 150))
        if sys_key == "gb":
            # Original Game Boy DMG-01 Putty Gray casing + Dark Gray dot-matrix bezel + D-pad/A/B
            fill_rect(canvas, 6, 16, 116, 94, (196, 192, 182, 255))
            draw_border(canvas, 6, 16, 116, 94, (112, 108, 102, 255), thickness=2)
            fill_rect(canvas, 12, 21, 104, 68, (96, 94, 102, 255))
            # Maroon & Navy DMG-01 header stripes + Red BATTERY LED
            fill_rect(canvas, 15, 23, 98, 1, (148, 34, 68, 255))
            fill_rect(canvas, 15, 25, 98, 1, (38, 48, 128, 255))
            fill_rect(canvas, 15, 50, 4, 4, (245, 42, 42, 255))
            scaled_scr = resize_rgba(primary_img, 84, 56)
            blit_image(canvas, scaled_scr, 24, 29)
            draw_border(canvas, 23, 28, 86, 58, (32, 42, 24, 255), thickness=1)
            # Bottom DMG-01 D-pad + Maroon A/B buttons
            fill_rect(canvas, 20, 94, 12, 4, (36, 38, 44, 255))
            fill_rect(canvas, 24, 90, 4, 12, (36, 38, 44, 255))
            fill_rect(canvas, 98, 92, 6, 6, (156, 28, 68, 255))
            fill_rect(canvas, 88, 96, 6, 6, (156, 28, 68, 255))
            draw_text(canvas, "GAME BOY", 38, 94, (36, 46, 118, 255), scale=1, shadow_color=None, max_width=48)
        elif sys_key == "gbc":
            # Game Boy Color Atomic Purple shell + Black curved lens + Rainbow C-O-L-O-R badge
            fill_rect(canvas, 6, 16, 116, 94, (112, 58, 178, 255))
            draw_border(canvas, 6, 16, 116, 94, (168, 112, 238, 255), thickness=2)
            fill_rect(canvas, 11, 20, 106, 74, (26, 28, 34, 255))
            fill_rect(canvas, 14, 48, 4, 4, (255, 48, 48, 255))
            scaled_scr = resize_rgba(primary_img, 86, 56)
            blit_image(canvas, scaled_scr, 23, 24)
            draw_border(canvas, 22, 23, 88, 58, (64, 68, 80, 255), thickness=1)
            draw_text(canvas, "GAME BOY", 22, 83, (190, 198, 212, 255), scale=1, shadow_color=None, max_width=52)
            for c_idx, (ch, c_col) in enumerate(
                zip("COLOR", [(238, 42, 72, 255), (148, 72, 225, 255), (96, 225, 64, 255), (255, 215, 42, 255), (48, 205, 238, 255)])
            ):
                draw_text(canvas, ch, 74 + c_idx * 6, 83, c_col, scale=1, shadow_color=None)
        elif sys_key == "nes":
            # NES-001 Two-Tone Gray Front-Loader Deck + Black Stripe + CRT Screen
            fill_rect(canvas, 6, 16, 116, 94, (208, 204, 198, 255))
            fill_rect(canvas, 6, 88, 116, 22, (122, 120, 124, 255))
            fill_rect(canvas, 84, 16, 22, 94, (28, 30, 34, 255))
            draw_border(canvas, 6, 16, 116, 94, (82, 80, 86, 255), thickness=2)
            scaled_scr = resize_rgba(primary_img, 96, 62)
            blit_image(canvas, scaled_scr, 14, 22)
            draw_border(canvas, 12, 20, 100, 66, (32, 34, 40, 255), thickness=2)
            # Red Power LED + Power/Reset buttons + Red NES label
            fill_rect(canvas, 14, 95, 5, 5, (255, 38, 38, 255))
            fill_rect(canvas, 24, 95, 14, 6, (58, 60, 66, 255))
            fill_rect(canvas, 42, 95, 14, 6, (58, 60, 66, 255))
            draw_text(canvas, "NES-001", 64, 95, (228, 38, 46, 255), scale=1, shadow_color=None, max_width=50)
        elif sys_key == "snes":
            # Super Nintendo SNS-001 / SFC Deck + Lavender Switches + 4-Color SFC Button Dots
            fill_rect(canvas, 6, 16, 116, 94, (194, 192, 202, 255))
            fill_rect(canvas, 12, 88, 104, 20, (148, 146, 160, 255))
            draw_border(canvas, 6, 16, 116, 94, (106, 102, 124, 255), thickness=2)
            scaled_scr = resize_rgba(primary_img, 96, 62)
            blit_image(canvas, scaled_scr, 14, 22)
            draw_border(canvas, 12, 20, 100, 66, (44, 42, 56, 255), thickness=2)
            # Lavender Power/Reset switches + 4-color SNES/SFC buttons (Blue, Green, Yellow, Red)
            fill_rect(canvas, 16, 93, 18, 6, (124, 96, 198, 255))
            fill_rect(canvas, 38, 93, 18, 6, (164, 148, 218, 255))
            for b_idx, b_col in enumerate(
                [(48, 108, 235, 255), (48, 198, 92, 255), (255, 212, 42, 255), (235, 48, 58, 255)]
            ):
                fill_rect(canvas, 78 + b_idx * 8, 93, 5, 5, b_col)
        elif sys_key == "md":
            # Sega Mega Drive 16-BIT Matte Black Deck + White/Red Ring + Gold 16-BIT Badge
            fill_rect(canvas, 6, 16, 116, 94, (26, 28, 36, 255))
            draw_border(canvas, 6, 16, 116, 94, (218, 176, 54, 255), thickness=2)
            scaled_scr = resize_rgba(primary_img, 96, 62)
            blit_image(canvas, scaled_scr, 14, 22)
            draw_border(canvas, 12, 20, 100, 66, (68, 76, 96, 255), thickness=2)
            # Red accent stripe + Gold 16-BIT + 3-button A/B/C row
            fill_rect(canvas, 12, 90, 104, 2, (218, 36, 48, 255))
            draw_text(canvas, "16-BIT", 14, 96, (248, 208, 68, 255), scale=1, shadow_color=None, max_width=42)
            for b_idx in range(3):
                fill_rect(canvas, 82 + b_idx * 10, 96, 6, 6, (88, 96, 118, 255))
        elif sys_key == "nds":
            # Nintendo DS NTR-001 Platinum Silver Clamshell + Central Hinge + Twin LEDs
            fill_rect(canvas, 6, 16, 116, 94, (192, 200, 214, 255))
            draw_border(canvas, 6, 16, 116, 94, (82, 94, 114, 255), thickness=2)
            # Central NDS Clamshell Hinge Bar at top of lower screen
            fill_rect(canvas, 8, 18, 112, 7, (44, 50, 62, 255))
            fill_rect(canvas, 102, 20, 4, 3, (52, 238, 98, 255))
            fill_rect(canvas, 108, 20, 4, 3, (255, 178, 42, 255))
            scaled_scr = resize_rgba(primary_img, 96, 64)
            blit_image(canvas, scaled_scr, 14, 28)
            draw_border(canvas, 12, 26, 100, 68, (34, 38, 48, 255), thickness=2)
            draw_text(canvas, "NINTENDO DS", 32, 97, (42, 48, 62, 255), scale=1, shadow_color=None, max_width=72)
        else:
            # Game Boy Advance AGB-001 Landscape Handheld (Side Grips + D-pad + A/B + L/R)
            fill_rect(canvas, 10, 15, 22, 4, (188, 194, 210, 255))  # L bumper
            fill_rect(canvas, 96, 15, 22, 4, (188, 194, 210, 255))  # R bumper
            fill_rect(canvas, 6, 18, 116, 90, (*theme.bezel_main, 255))
            draw_border(canvas, 6, 18, 116, 90, theme.border_outer, thickness=2)
            # Left D-pad & Right A/B buttons on side wings
            fill_rect(canvas, 9, 56, 9, 3, (205, 212, 226, 255))
            fill_rect(canvas, 12, 53, 3, 9, (205, 212, 226, 255))
            fill_rect(canvas, 112, 52, 4, 4, (205, 212, 226, 255))
            fill_rect(canvas, 107, 57, 4, 4, (205, 212, 226, 255))
            fill_rect(canvas, 113, 42, 3, 3, theme.bezel_led)
            # Dark GBA screen lens + 3:2 screen
            fill_rect(canvas, 20, 22, 88, 78, (32, 36, 46, 255))
            scaled_scr = resize_rgba(primary_img, 80, 60)
            blit_image(canvas, scaled_scr, 24, 26)
            draw_text(canvas, "GAME BOY ADV.", 25, 89, (195, 205, 225, 255), scale=1, shadow_color=None, max_width=80)
        right_x = 128

    max_text_w = 256 - right_x - 6
    sys_label, sys_prefix, _, _, _ = CONSOLE_THEMES.get(sys_key, CONSOLE_THEMES["gba"])

    # Right side top row: Console pill badge + 16x16 Pixel Emblem on far right
    emblem_x = 256 - 21
    pill_w = max(60, emblem_x - right_x - 3)
    fill_rect(canvas, right_x, 13, pill_w, 13, theme.accent_secondary)
    draw_border(canvas, right_x, 13, pill_w, 13, theme.border_outer, thickness=1)
    draw_text(
        canvas,
        sys_label,
        right_x + 3,
        16,
        (255, 255, 255, 255),
        scale=1,
        shadow_color=None,
        max_width=pill_w - 5,
    )
    draw_pixel_emblem(canvas, theme.emblem, emblem_x, 11, scale=1, shadow=True)

    # Wrap game title into up to 3 lines
    max_chars = max(10, max_text_w // 6)
    words = meta.short_title.split()
    lines: List[str] = []
    cur = ""
    for w_word in words:
        test = f"{cur} {w_word}".strip()
        if len(test) <= max_chars:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w_word[:max_chars]
    if cur:
        lines.append(cur)
    if not lines:
        lines = [meta.short_title[:max_chars]]

    ty = 33
    for idx, line in enumerate(lines[:3]):
        color = (255, 255, 255, 255) if idx == 0 else (235, 240, 255, 255)
        draw_text(canvas, line, right_x, ty, color, scale=1, max_width=max_text_w)
        ty += 11

    # Themed divider line with diamond stud
    div_y = max(ty + 3, 71)
    fill_rect(canvas, right_x, div_y, max_text_w, 1, theme.border_inner)
    fill_rect(canvas, right_x, div_y, 28, 2, theme.accent_primary)

    # Publisher, Release Year & Genre Tag
    draw_text(
        canvas,
        f"Released: {meta.release_year}",
        right_x,
        div_y + 6,
        (210, 222, 248, 255),
        scale=1,
        max_width=max_text_w,
    )
    draw_text(
        canvas,
        meta.publisher,
        right_x,
        div_y + 18,
        (185, 200, 232, 255),
        scale=1,
        max_width=max_text_w,
    )

    # Bottom row: Cartridge Serial Pill + Genre Pill
    serial_str = f"{sys_prefix}-{meta.game_code[:4].upper()}"
    ser_w = len(serial_str) * 6 + 6
    fill_rect(canvas, right_x, div_y + 29, ser_w, 11, (16, 18, 30, 230))
    draw_border(canvas, right_x, div_y + 29, ser_w, 11, theme.accent_primary, thickness=1)
    draw_text(
        canvas,
        serial_str,
        right_x + 3,
        div_y + 31,
        theme.accent_primary,
        scale=1,
        shadow_color=None,
    )

    genre_tag = theme.badge_short
    gt_w = len(genre_tag) * 6 + 8
    gt_x = right_x + ser_w + 5
    if gt_x + gt_w <= 250:
        fill_rect(canvas, gt_x, div_y + 29, gt_w, 11, theme.accent_secondary)
        draw_border(canvas, gt_x, div_y + 29, gt_w, 11, theme.border_outer, thickness=1)
        draw_text(
            canvas,
            genre_tag,
            gt_x + 4,
            div_y + 31,
            (255, 255, 255, 255),
            scale=1,
            shadow_color=None,
        )

    draw_border(canvas, 0, 0, 256, 128, theme.border_outer, thickness=1)
    draw_corner_ornaments(canvas, 1, 4, 254, 119, theme, size=3)
    return canvas
