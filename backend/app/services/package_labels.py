"""Portable package-label rendering.

The browser can print the returned PNG/HTML today while a local print bridge
claims the same durable job later.  Labels deliberately encode only a stable,
authenticated lookup URL and package code; neither a bearer token nor product
facts that may later change are baked into the identity.
"""
from __future__ import annotations

import base64
import io
import re
from typing import Any

import qrcode
from PIL import Image, ImageDraw, ImageFont


PAPER_PROFILES = {
    "thermal_4x6": (1200, 1800, "4in 6in"),
    "thermal_54x100mm": (638, 1181, "54mm 100mm"),
    "receipt_58mm": (696, 384, "58mm auto"),
    # Native dot canvas rounded from 58 x 40 mm at the verified WT20 203 DPI.
    "thermal_58x40mm": (464, 320, "58mm 40mm"),
}

INVENTORY_LABEL_DPI = 203
INVENTORY_LABEL_TEMPLATE_VERSION = 2


def normalize_profile(value: str | None) -> str:
    profile = str(value or "thermal_4x6").strip().lower()
    if profile not in PAPER_PROFILES:
        raise ValueError("paper_profile must be thermal_4x6, thermal_54x100mm, receipt_58mm, or thermal_58x40mm")
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


def code128_layout(code: str, max_width: int, quiet_zone: int = 10, minimum_module_dots: int = 2) -> tuple[list[int], int, int]:
    """Return symbols, integer module width, and total width; never squeeze bars."""
    symbols = code128_values(code)
    if not 10 <= int(quiet_zone) <= 40:
        raise ValueError("Code 128 quiet zones must be between 10 and 40 modules.")
    if minimum_module_dots < 1:
        raise ValueError("minimum_module_dots must be positive.")
    modules = sum(sum(int(width) for width in _CODE128_PATTERNS[symbol]) for symbol in symbols)
    total_modules = modules + int(quiet_zone) * 2
    unit = max_width // max(total_modules, 1)
    if unit < minimum_module_dots:
        raise ValueError(f"Code 128 needs at least {total_modules * minimum_module_dots} dots including quiet zones; only {max_width} are available.")
    return symbols, unit, total_modules * unit


def _code128(draw: ImageDraw.ImageDraw, code: str, x: int, y: int, height: int, max_width: int, quiet_zone: int = 10, minimum_module_dots: int = 2) -> tuple[int, int]:
    symbols, unit, total_width = code128_layout(code, max_width, quiet_zone, minimum_module_dots)
    # Quiet zone before/after is mandated for hardware scanner reliability.
    x += unit * int(quiet_zone)
    for symbol in symbols:
        black = True
        for width_token in _CODE128_PATTERNS[symbol]:
            width = int(width_token) * unit
            if black:
                draw.rectangle((x, y, x + width - 1, y + height - 1), fill="black")
            x += width
            black = not black
    return unit, total_width


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


INVENTORY_LABEL_TEMPLATE = {
    "version": INVENTORY_LABEL_TEMPLATE_VERSION, "coordinate_unit": "mm", "dpi": INVENTORY_LABEL_DPI,
    "margin_mm": 2.0,
    "title_x_mm": 2.5, "title_y_mm": 2.0, "title_width_mm": 36.0, "title_size_mm": 2.5,
    "sku_x_mm": 2.5, "sku_y_mm": 7.3, "sku_width_mm": 36.0, "sku_size_mm": 2.0,
    "unit_x_mm": 2.5, "unit_y_mm": 12.1, "unit_size_mm": 1.9,
    "shelf_x_mm": 2.5, "shelf_y_mm": 16.1, "shelf_width_mm": 35.0, "shelf_size_mm": 4.2,
    "qr_x_mm": 40.0, "qr_y_mm": 2.0, "qr_size_mm": 15.0,
    "barcode_x_mm": 2.5, "barcode_y_mm": 30.0, "barcode_width_mm": 53.0,
    "barcode_height_mm": 6.0, "barcode_quiet_zone_modules": 10, "barcode_min_module_dots": 2,
    "print_scale_percent": 100,
}

_LEGACY_TEMPLATE_KEYS = {
    "margin": "margin_mm", "title_x": "title_x_mm", "title_y": "title_y_mm", "title_size": "title_size_mm",
    "sku_x": "sku_x_mm", "sku_y": "sku_y_mm", "sku_size": "sku_size_mm",
    "unit_x": "unit_x_mm", "unit_y": "unit_y_mm", "unit_size": "unit_size_mm",
    "shelf_x": "shelf_x_mm", "shelf_y": "shelf_y_mm", "shelf_size": "shelf_size_mm",
    "qr_x": "qr_x_mm", "qr_y": "qr_y_mm", "qr_size": "qr_size_mm",
    "barcode_x": "barcode_x_mm", "barcode_y": "barcode_y_mm", "barcode_width": "barcode_width_mm",
    "barcode_height": "barcode_height_mm", "barcode_quiet_zone": "barcode_quiet_zone_modules",
}


def normalize_inventory_template(saved: dict[str, Any] | None) -> dict[str, Any]:
    """Convert saved Phase 1 300-DPI pixel coordinates to physical mm."""
    source = dict(saved or {})
    try:
        version = int(source.get("version", 1))
    except (TypeError, ValueError):
        raise ValueError("Label template version must be an integer.")
    if not source or version >= INVENTORY_LABEL_TEMPLATE_VERSION:
        result = {**INVENTORY_LABEL_TEMPLATE, **source}
        result.update(version=INVENTORY_LABEL_TEMPLATE_VERSION, coordinate_unit="mm", dpi=INVENTORY_LABEL_DPI)
        return result
    result = dict(INVENTORY_LABEL_TEMPLATE)
    for old_key, new_key in _LEGACY_TEMPLATE_KEYS.items():
        if old_key in source:
            value = float(source[old_key])
            result[new_key] = max(10, min(40, round(value))) if old_key == "barcode_quiet_zone" else round(value * 25.4 / 300.0, 3)
    if "print_scale_percent" in source:
        result["print_scale_percent"] = int(source["print_scale_percent"])
    result.update(version=INVENTORY_LABEL_TEMPLATE_VERSION, coordinate_unit="mm", dpi=INVENTORY_LABEL_DPI, template_migrated_from=1)
    return result


def _dots(mm: float, dpi: int = INVENTORY_LABEL_DPI) -> int:
    return int(round(float(mm) * dpi / 25.4))


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    value = str(text or "")
    if max_width <= 0:
        raise ValueError("Text width must be positive.")
    if draw.textbbox((0, 0), value, font=font)[2] <= max_width:
        return value
    suffix = "..."
    while value and draw.textbbox((0, 0), value + suffix, font=font)[2] > max_width:
        value = value[:-1]
    return value + suffix if value else ""


def render_inventory_label(payload: dict[str, Any], paper_profile: str = "thermal_58x40mm", *, layout: str = "qr", template: dict[str, Any] | None = None) -> bytes:
    """Render a 1-bit native-203-DPI label in physical mm coordinates."""
    profile = normalize_profile(paper_profile)
    if profile != "thermal_58x40mm":
        raise ValueError("Inventory labels require the 58 x 40 mm profile.")
    width, height, _ = PAPER_PROFILES[profile]
    config = normalize_inventory_template(template)
    image = Image.new("1", (width, height), 1)
    draw = ImageDraw.Draw(image)
    margin = _dots(config["margin_mm"])
    title = str(payload.get("title") or "UNMATCHED PRODUCT")[:128]
    unit = str(payload.get("unit_id") or "")
    shelf = str(payload.get("shelf_code") or payload.get("location_key") or "")
    sku = str(payload.get("sku") or "")[:32]
    token = str(payload.get("barcode_token") or "")
    if layout not in {"qr", "code128", "qr_code128"}:
        raise ValueError("Choose qr, code128, or qr_code128 layout.")
    if layout in {"code128", "qr_code128"} and not re.fullmatch(r"[A-Z2-7]{13}", token):
        raise ValueError("A persisted 13-character item barcode token is required for Code 128.")
    elements = {
        "title": ("title_x_mm", "title_y_mm", "title_width_mm", "title_size_mm", title),
        "sku": ("sku_x_mm", "sku_y_mm", "sku_width_mm", "sku_size_mm", f"SKU {sku}"),
        "unit": ("unit_x_mm", "unit_y_mm", "title_width_mm", "unit_size_mm", f"UNIT {unit}"),
        "shelf": ("shelf_x_mm", "shelf_y_mm", "shelf_width_mm", "shelf_size_mm", shelf),
    }
    for x_key, y_key, w_key, size_key, text in elements.values():
        x, y, w, size = (_dots(config[key]) for key in (x_key, y_key, w_key, size_key))
        if min(x, y) < margin or w <= 0 or x + w > width - margin or y + size > height - margin:
            raise ValueError(f"Template element {x_key}/{y_key} exceeds canvas margins.")
        font = _font(size, bold=True)
        draw.text((x, y), _fit_text(draw, text, font, w), font=font, fill=0)
    if layout in {"qr", "qr_code128"}:
        lookup_url = str(payload.get("lookup_url") or "")
        if not lookup_url.startswith(("https://", "http://")):
            raise ValueError("QR identity must be a canonical PosterPro URL.")
        qr = qrcode.QRCode(border=2, box_size=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
        qr.add_data(lookup_url); qr.make(fit=True)
        qr_image = qr.make_image(fill_color="black", back_color="white").convert("1")
        qr_size = _dots(config["qr_size_mm"])
        qr_x, qr_y = _dots(config["qr_x_mm"]), _dots(config["qr_y_mm"])
        if qr_size <= 0 or qr_x < margin or qr_y < margin:
            raise ValueError("QR position and size must fit inside canvas margins.")
        modules = qr_image.width
        module_dots = qr_size // modules
        if module_dots < 2:
            raise ValueError(f"QR needs at least {modules * 2} dots at this payload size; only {qr_size} are configured.")
        qr_actual_size = modules * module_dots
        if qr_x + qr_actual_size > width - margin or qr_y + qr_actual_size > height - margin:
            raise ValueError("QR position and integer module size exceed canvas margins.")
        qr_image = qr_image.resize((qr_actual_size, qr_actual_size), Image.Resampling.NEAREST)
        image.paste(qr_image, (qr_x, qr_y))
    if layout in {"code128", "qr_code128"}:
        x, y = _dots(config["barcode_x_mm"]), _dots(config["barcode_y_mm"])
        barcode_width, barcode_height = _dots(config["barcode_width_mm"]), _dots(config["barcode_height_mm"])
        quiet, minimum = int(config["barcode_quiet_zone_modules"]), int(config["barcode_min_module_dots"])
        if x < margin or y < margin or x + barcode_width > width - margin or y + barcode_height > height - margin:
            raise ValueError("Code 128 position and size must fit inside canvas margins.")
        if barcode_height < _dots(5):
            raise ValueError("Code 128 height must be at least 5 mm.")
        _, _, rendered_width = code128_layout(token, barcode_width, quiet, minimum)
        _code128(draw, token, x, y, barcode_height, barcode_width, quiet, minimum)
        caption_font = _font(_dots(1.4))
        caption_y = y + barcode_height + 1
        if caption_y + _dots(1.7) > height - margin:
            raise ValueError("Code 128 human-readable token would be clipped.")
        draw.text((x, caption_y), _fit_text(draw, token, caption_font, rendered_width), font=caption_font, fill=0)
    buffer = io.BytesIO(); image.save(buffer, format="PNG", dpi=(INVENTORY_LABEL_DPI, INVENTORY_LABEL_DPI))
    return buffer.getvalue()


def render_inventory_calibration() -> bytes:
    width, height, _ = PAPER_PROFILES["thermal_58x40mm"]
    image = Image.new("1", (width, height), 1)
    draw = ImageDraw.Draw(image)
    draw.rectangle((1, 1, width - 2, height - 2), outline=0, width=1)
    step = _dots(10)
    for x in range(0, width, step): draw.line((x, 0, x, height), fill=0, width=1)
    for y in range(0, height, step): draw.line((0, y, width, y), fill=0, width=1)
    draw.text((_dots(2), _dots(2)), "58 x 40 mm / 203 DPI", font=_font(_dots(2.2), bold=True), fill=0)
    draw.line((_dots(2), _dots(8), width - _dots(2), _dots(8)), fill=0, width=1)
    return image_to_png(image, dpi=INVENTORY_LABEL_DPI)


def image_to_png(image: Image.Image, dpi: int | None = None) -> bytes:
    buffer = io.BytesIO(); image.save(buffer, format="PNG", **({"dpi": (dpi, dpi)} if dpi else {})); return buffer.getvalue()
