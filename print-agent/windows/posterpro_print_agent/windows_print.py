"""WT20 print adapter. Uses Windows GDI plus the installed WT20 driver only."""
from __future__ import annotations

import io
from typing import Any

from .core import PRINTER_NAME


def _win32():
    try:
        import win32con
        import win32gui
        import win32print
        import win32ui
        from PIL import Image, ImageWin
    except ImportError as exc:
        raise RuntimeError("Windows printing requires pywin32 and Pillow; install the pinned requirements file.") from exc
    return win32con, win32gui, win32print, win32ui, Image, ImageWin


def find_58x40_form(printer_name: str) -> str:
    win32con, _, win32print, _, _, _ = _win32()
    if printer_name != PRINTER_NAME:
        raise RuntimeError("Refusing to inspect a printer outside the allowlist.")
    names = {printer[2] for printer in win32print.EnumPrinters(win32con.PRINTER_ENUM_LOCAL | win32con.PRINTER_ENUM_CONNECTIONS)}
    if printer_name not in names:
        raise RuntimeError(f"Configured printer queue {printer_name!r} is missing; no fallback printer will be selected.")
    handle = win32print.OpenPrinter(printer_name)
    try:
        forms = win32print.EnumForms(handle, 1)
    finally:
        win32print.ClosePrinter(handle)
    matches = []
    for form in forms:
        size = form.get("Size") or {}
        # FORM_INFO_1 sizes use 0.001 mm units. Do not substitute the current
        # queue default; the exact installed 58x40 form must be enumerated.
        if isinstance(size, dict):
            width, height = size.get("cx", 0), size.get("cy", 0)
        else:
            width, height = size[0], size[1]
        if abs(int(width) - 58000) <= 50 and abs(int(height) - 40000) <= 50:
            matches.append(form["Name"])
    if not matches:
        raise RuntimeError("WT20 has no enumerated 58x40 mm form; select/verify its existing driver preset before printing.")
    if len(matches) > 1:
        preferred = [name for name in matches if "58" in name and "40" in name]
        if len(preferred) != 1:
            raise RuntimeError("Multiple indistinguishable 58x40 mm forms exist; explicit profile selection is required.")
        return preferred[0]
    return matches[0]


def print_raster(printer_name: str, png: bytes, document_name: str, *, dry_run: bool = False) -> str | None:
    if dry_run:
        return "DRY-RUN"
    win32con, win32gui, win32print, win32ui, Image, ImageWin = _win32()
    if printer_name != PRINTER_NAME:
        raise RuntimeError("Refusing to print to a printer outside the allowlist.")
    form_name = find_58x40_form(printer_name)
    printer = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(printer, 2)
        devmode = info["pDevMode"]
        devmode.FormName = form_name
        devmode.Orientation = win32con.DMORIENT_PORTRAIT
        devmode.PrintQuality = 203
        devmode.YResolution = 203
        devmode.Fields |= win32con.DM_FORMNAME | win32con.DM_ORIENTATION | win32con.DM_PRINTQUALITY | win32con.DM_YRESOLUTION
        # Use a per-job DEVMODE, never SetPrinter/registry/default-printer edits.
        hdc = win32gui.CreateDC("WINSPOOL", printer_name, None, devmode)
        dc = win32ui.CreateDCFromHandle(hdc)
        image = Image.open(io.BytesIO(png)).convert("1")
        if image.size != (464, 320):
            raise RuntimeError("Native label raster has unexpected dimensions.")
        spooler_id = dc.StartDoc(document_name)
        try:
            dc.StartPage()
            ImageWin.Dib(image).draw(dc.GetHandleOutput(), (0, 0, 464, 320))
            dc.EndPage()
        finally:
            # EndDoc returning means only that GDI accepted the document.
            dc.EndDoc()
        # The driver/spooler job ID is not consistently exposed by GDI.
        return str(spooler_id) if isinstance(spooler_id, int) and spooler_id > 0 else None
    finally:
        win32print.ClosePrinter(printer)


def queue_status(printer_name: str = PRINTER_NAME) -> dict[str, Any]:
    win32con, _, win32print, _, _, _ = _win32()
    if printer_name != PRINTER_NAME:
        return {"status": "printer_missing", "printer_name": printer_name}
    printers = {entry[2]: entry for entry in win32print.EnumPrinters(win32con.PRINTER_ENUM_LOCAL | win32con.PRINTER_ENUM_CONNECTIONS)}
    entry = printers.get(printer_name)
    if not entry:
        return {"status": "printer_missing", "printer_name": printer_name}
    handle = win32print.OpenPrinter(printer_name)
    try:
        info = win32print.GetPrinter(handle, 2)
        status = int(info.get("Status") or 0)
        return {"status": "printer_offline" if status & win32con.PRINTER_STATUS_OFFLINE else "online", "printer_name": printer_name, "driver": info.get("pDriverName"), "port": info.get("pPortName"), "queue_status_flags": status}
    finally:
        win32print.ClosePrinter(handle)
