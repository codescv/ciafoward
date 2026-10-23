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

from .metadata import GameMetadata

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


def generate_stylized_cover(meta: GameMetadata, width: int = 240, height: int = 160) -> RGBAImage:
    """
    Generate a clean, stylized GBA cartridge/screen cover image when offline or no boxart is found.
    """
    img = RGBAImage(width, height)
    # Deep indigo GBA gradient background
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(28 + 20 * (1.0 - t))
        g = int(32 + 28 * (1.0 - t))
        b = int(85 + 65 * (1.0 - t))
        row = y * width
        for x in range(width):
            img.pixels[row + x] = (r, g, b, 255)

    # Top header bar
    fill_rect(img, 0, 0, width, 26, (18, 20, 48, 255))
    fill_rect(img, 0, 26, width, 2, (110, 140, 255, 255))
    draw_text(img, "GAME BOY ADVANCE", 10, 9, (190, 210, 255, 255), scale=1)

    # Wrap and render title
    words = meta.short_title.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        test = f"{cur} {w}".strip()
        if len(test) <= 16:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w[:16]
    if cur:
        lines.append(cur)
    if not lines:
        lines = [meta.game_code]

    y_pos = 44
    for line in lines[:3]:
        draw_text(img, line, 12, y_pos, (255, 255, 255, 255), scale=2, max_width=width - 24)
        y_pos += 20

    # Bottom info strip
    fill_rect(img, 0, height - 28, width, 28, (14, 16, 36, 230))
    draw_text(
        img,
        f"{meta.publisher[:18]} ({meta.release_year})",
        10,
        height - 18,
        (170, 185, 220, 255),
        scale=1,
        max_width=width - 80,
    )
    draw_text(
        img,
        meta.game_code,
        width - 42,
        height - 18,
        (255, 215, 90, 255),
        scale=1,
    )
    draw_border(img, 0, 0, width, height, (90, 115, 200, 255), thickness=2)
    return img


def compose_vc_icons(
    meta: GameMetadata,
    source_image: Optional[RGBAImage] = None,
) -> Tuple[RGBAImage, RGBAImage]:
    """
    Create the 24x24 and 48x48 3DS SMDH icons with a Virtual Console bezel frame.
    Returns (icon_24x24, icon_48x48).
    """
    if source_image is None:
        if meta.boxart_path and os.path.exists(meta.boxart_path):
            source_image = load_image_file(meta.boxart_path)
        elif meta.title_screen_path and os.path.exists(meta.title_screen_path):
            source_image = load_image_file(meta.title_screen_path)
        else:
            source_image = generate_stylized_cover(meta, 128, 128)

    # Build 48x48 icon: outer 3DS VC dark bezel + 42x42 inner art
    icon48 = RGBAImage(48, 48, [(24, 26, 36, 255)] * (48 * 48))
    draw_border(icon48, 0, 0, 48, 48, (75, 82, 105, 255), thickness=1)
    draw_border(icon48, 1, 1, 46, 46, (38, 42, 58, 255), thickness=1)
    inner48 = resize_rgba(source_image, 42, 42)
    blit_image(icon48, inner48, 3, 3)
    draw_border(icon48, 2, 2, 44, 44, (14, 16, 22, 255), thickness=1)

    # Build 24x24 icon: outer 1px border + 22x22 inner art
    icon24 = RGBAImage(24, 24, [(24, 26, 36, 255)] * (24 * 24))
    inner24 = resize_rgba(source_image, 22, 22)
    blit_image(icon24, inner24, 1, 1)
    draw_border(icon24, 0, 0, 24, 24, (65, 72, 95, 255), thickness=1)

    return icon24, icon48


def compose_vc_banner_256x128(
    meta: GameMetadata,
    boxart_img: Optional[RGBAImage] = None,
    title_screen_img: Optional[RGBAImage] = None,
    banner_style: str = "vc",
) -> RGBAImage:
    """
    Create the 256x128 top-screen 3DS Banner image.
    - `banner_style == "vc"` (default): Nintendo 3DS Virtual Console showcase card with
      Boxart/Screen preview on the left and GBA Virtual Console metadata plate on the right.
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

    primary_img = boxart_img or title_screen_img or generate_stylized_cover(meta, 240, 160)

    if banner_style == "full":
        return resize_rgba(primary_img, 256, 128)

    # Create 256x128 canvas with a sleek dark metallic VC backdrop
    canvas = RGBAImage(256, 128)
    for y in range(128):
        t = y / 127.0
        r = int(20 + 18 * (1.0 - t))
        g = int(24 + 22 * (1.0 - t))
        b = int(42 + 38 * (1.0 - t))
        row = y * 256
        for x in range(256):
            canvas.pixels[row + x] = (r, g, b, 255)

    # Top & bottom accent bars
    fill_rect(canvas, 0, 0, 256, 4, (92, 112, 215, 255))
    fill_rect(canvas, 0, 125, 256, 3, (62, 78, 165, 255))

    # Left side: Boxart &/or GBA Screen preview inside a framed card
    if boxart_img is not None:
        # Fit boxart cleanly into a 96x96 box at (12, 16)
        fill_rect(canvas, 14, 18, 96, 96, (0, 0, 0, 140))  # drop shadow
        scaled_box = resize_rgba(boxart_img, 94, 94)
        fill_rect(canvas, 10, 14, 98, 98, (230, 235, 250, 255))
        blit_image(canvas, scaled_box, 12, 16)
        draw_border(canvas, 10, 14, 98, 98, (65, 80, 140, 255), thickness=1)
        right_x = 118
    else:
        # 3:2 GBA Screen aspect ratio (114x76) at (10, 26)
        fill_rect(canvas, 12, 28, 118, 80, (0, 0, 0, 140))
        scaled_scr = resize_rgba(primary_img, 114, 76)
        fill_rect(canvas, 8, 24, 118, 80, (42, 46, 68, 255))
        blit_image(canvas, scaled_scr, 10, 26)
        draw_border(canvas, 8, 24, 118, 80, (110, 130, 210, 255), thickness=1)
        right_x = 134

    max_text_w = 256 - right_x - 8

    # Right side: "VIRTUAL CONSOLE" & "GAME BOY ADVANCE" pill badge
    fill_rect(canvas, right_x, 14, max_text_w, 13, (78, 96, 196, 255))
    draw_text(
        canvas,
        "GAME BOY ADVANCE",
        right_x + 4,
        17,
        (255, 255, 255, 255),
        scale=1,
        shadow_color=None,
        max_width=max_text_w - 6,
    )

    # Wrap game title into up to 3 lines
    max_chars = max(10, max_text_w // 6)
    words = meta.short_title.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        test = f"{cur} {w}".strip()
        if len(test) <= max_chars:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w[:max_chars]
    if cur:
        lines.append(cur)
    if not lines:
        lines = [meta.short_title[:max_chars]]

    ty = 35
    for idx, line in enumerate(lines[:3]):
        color = (255, 255, 255, 255) if idx == 0 else (230, 236, 255, 255)
        draw_text(canvas, line, right_x, ty, color, scale=1, max_width=max_text_w)
        ty += 11

    # Divider line
    div_y = max(ty + 4, 74)
    fill_rect(canvas, right_x, div_y, max_text_w, 1, (75, 90, 145, 255))

    # Publisher & Release Year
    draw_text(
        canvas,
        f"Released: {meta.release_year}",
        right_x,
        div_y + 7,
        (185, 198, 230, 255),
        scale=1,
        max_width=max_text_w,
    )
    draw_text(
        canvas,
        meta.publisher,
        right_x,
        div_y + 19,
        (165, 180, 215, 255),
        scale=1,
        max_width=max_text_w,
    )
    draw_text(
        canvas,
        f"AGB-{meta.game_code.upper()}",
        right_x,
        div_y + 31,
        (255, 215, 95, 255),
        scale=1,
        max_width=max_text_w,
    )

    draw_border(canvas, 0, 0, 256, 128, (90, 110, 190, 255), thickness=1)
    return canvas
