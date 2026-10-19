"""Portable package-label rendering.

The browser can print the returned PNG/HTML today while a local print bridge
claims the same durable job later.  Labels deliberately encode only a stable,
authenticated lookup URL and package code; neither a bearer token nor product
facts that may later change are baked into the identity.
"""
from __future__ import annotations

import base64
import io
from typing import Any

import qrcode
from PIL import Image, ImageDraw, ImageFont


PAPER_PROFILES = {
    "thermal_4x6": (1200, 1800, "4in 6in"),
    "thermal_54x100mm": (638, 1181, "54mm 100mm"),
    "receipt_58mm": (696, 384, "58mm auto"),
}


def normalize_profile(value: str | None) -> str:
    profile = str(value or "thermal_4x6").strip().lower()
    if profile not in PAPER_PROFILES:
        raise ValueError("paper_profile must be thermal_4x6, thermal_54x100mm, or receipt_58mm")
    return profile


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


# ISO/IEC 15417 Code 128 module widths, values 0–106 (the last is STOP).
_CODE128_PATTERNS = (
    "212222 222122 222221 121223 121322 131222 122213 122312 132212 221213 221312 231212 112232 122132 122231 113222 123122 123221 223211 221132 221231 213212 223112 312131 311222 321122 321221 312212 322112 322211 212123 212321 232121 111323 131123 131321 112313 132113 132311 211313 231113 231311 112133 112331 132131 113123 113321 133121 313121 211331 231131 213113 213311 213131 311123 311321 331121 312113 312311 332111 314111 221411 431111 111224 111422 121124 121421 141122 141221 112214 112412 122114 122411 142112 142211 241211 221114 413111 241112 134111 111242 121142 121241 114212 124112 124211 411212 421112 421211 212141 214121 412121 111143 111341 131141 114113 114311 411113 411311 113141 114131 311141 411131 211412 211214 211232 2331112"
).split()


def code128_values(value: str) -> list[int]:
    """Encode printable ASCII as Code 128 set B with its required checksum."""
    safe = "".join(char if 32 <= ord(char) <= 126 else "-" for char in value)[:48] or "PACKAGE"
    symbols = [104, *(ord(char) - 32 for char in safe)]  # Start Code B.
    checksum = (symbols[0] + sum(symbol * index for index, symbol in enumerate(symbols[1:], 1))) % 103
    return [*symbols, checksum, 106]


def _code128(draw: ImageDraw.ImageDraw, code: str, x: int, y: int, height: int, max_width: int) -> None:
    symbols = code128_values(code)
    modules = sum(sum(int(width) for width in _CODE128_PATTERNS[symbol]) for symbol in symbols) + 20  # quiet zones
    unit = max(1, min(8, max_width // max(modules, 1)))
    # Quiet zone before/after is mandated for hardware scanner reliability.
    x += unit * 10
    for symbol in symbols:
        black = True
        for width_token in _CODE128_PATTERNS[symbol]:
            width = int(width_token) * unit
            if black:
                draw.rectangle((x, y, x + width - 1, y + height), fill="black")
            x += width
            black = not black


def render_package_label(payload: dict[str, Any], paper_profile: str) -> bytes:
    profile = normalize_profile(paper_profile)
    width, height, _ = PAPER_PROFILES[profile]
    margin = max(20, width // 24)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title = str(payload.get("title") or "PACKAGED INVENTORY").strip()
    code = str(payload.get("package_code") or "PACKAGE").upper()
    lookup_url = str(payload.get("lookup_url") or "")
    quantity = int(payload.get("quantity") or 0)
    location = str(payload.get("parent_location_label") or payload.get("parent_location_key") or "Outside tote")
    draw.rectangle((margin, margin, width - margin, height - margin), outline="black", width=max(2, width // 320))
    draw.text((margin * 2, margin * 2), "POSTERPRO  •  PACKAGE", font=_font(max(16, width // 38), bold=True), fill="black")
    draw.text((margin * 2, margin * 4), title[:70], font=_font(max(18, width // 30), bold=True), fill="black")
    draw.text((margin * 2, margin * 6), f"PACKAGE {code}", font=_font(max(18, width // 32), bold=True), fill="black")
    draw.text((margin * 2, margin * 8), f"QTY {quantity}  •  {location[:60]}", font=_font(max(14, width // 48)), fill="black")
    qr_size = min(width // 3, height // 3)
    qr = qrcode.QRCode(border=2, box_size=8)
    qr.add_data(lookup_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB").resize((qr_size, qr_size))
    qr_x, qr_y = width - margin * 2 - qr_size, margin * 10
    image.paste(qr_img, (qr_x, qr_y))
    draw.text((qr_x, qr_y + qr_size + 8), "Private package lookup", font=_font(max(12, width // 62)), fill="black")
    barcode_y = max(margin * 12 + qr_size, height - max(190, height // 5))
    _code128(draw, code, margin * 2, barcode_y, max(70, height // 18), width - margin * 4)
    draw.text((margin * 2, barcode_y + max(84, height // 15)), code, font=_font(max(14, width // 45), bold=True), fill="black")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    return buffer.getvalue()


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
